import asyncio
import io
import qrcode
import time
import socket
import os
import sys
from websockets import ConnectionClosedOK
from pydglab_ws import DGLabWSConnect, StrengthData, FeedbackButton, Channel, StrengthOperationType, RetCode

# 导入配置文件
if getattr(sys, 'frozen', False):
    base_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, base_dir)
from config import (
    PULSE_DATA,
    CURRENT_WAVEFORM_A,
    CURRENT_WAVEFORM_B,
    WAVEFORM_SEND_INTERVAL,
    CONNECTION_TIMEOUT
)

# 全局变量
client = None
control_task = None

# 波形列表
available_waveforms = list(PULSE_DATA.keys())

# 根据config中的波形名称设置初始索引
try:
    current_waveform_index_a = available_waveforms.index(CURRENT_WAVEFORM_A)
except ValueError:
    current_waveform_index_a = 0  # 如果波形不存在，使用第一个

try:
    current_waveform_index_b = available_waveforms.index(CURRENT_WAVEFORM_B)
except ValueError:
    current_waveform_index_b = 0  # 如果波形不存在，使用第一个

# 记录每个通道上一次发送的波形名称
last_waveform_name_a = None
last_waveform_name_b = None

# SimpleControl实例
simple_control = None

# 记录上一次的强度值，用于避免重复打印
last_strength_a = 1
last_strength_b = 1

# 记录上一次的手机上限，用于避免重复打印
last_a_limit = None
last_b_limit = None


def get_host_ip():
    """获取本机IP地址"""
    ss = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        ss.connect(('8.8.8.8', 80))
        ip = ss.getsockname()[0]
    finally:
        ss.close()
    return ip


def print_qrcode(data: str):
    """生成并显示二维码"""
    try:
        qr_ascii = qrcode.QRCode()
        qr_ascii.add_data(data)
        f = io.StringIO()
        qr_ascii.print_ascii(out=f)
        f.seek(0)
        print("请用 DG-Lab App 扫描以下二维码:")
        print(f.read())

        qr_png = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr_png.add_data(data)
        qr_png.make(fit=True)

        img = qr_png.make_image(fill_color="black", back_color="white")
        qr_code_path = os.path.join(base_dir, "dg_lab_qrcode.png")
        img.save(qr_code_path)
        img.show()

        print(f"二维码PNG文件已保存为: {qr_code_path}")

    except Exception as e:
        print(f"生成二维码时出错: {e}")
        qr = qrcode.QRCode()
        qr.add_data(data)
        f = io.StringIO()
        qr.print_ascii(out=f)
        f.seek(0)
        print("请用 DG-Lab App 扫描以下二维码:")
        print(f.read())


class SimpleControl:
    """简化控制类"""

    def __init__(self):
        self.current_strength_a = 1
        self.current_strength_b = 1
        self.a_limit = 999  # 初始值，将从手机获取
        self.b_limit = 999  # 初始值，将从手机获取
        self.is_paused = False
        self.protect_active = False
        self.output_active = True

    def update_limits(self, a_limit, b_limit):
        """更新手机强度上限，只有变化时才打印"""
        global last_a_limit, last_b_limit

        # 检查是否有变化
        changed = False
        if a_limit != last_a_limit:
            changed = True
        if b_limit != last_b_limit:
            changed = True

        # 如果发生变化，则更新并打印
        if changed:
            self.a_limit = a_limit
            self.b_limit = b_limit
            print(f"手机强度上限更新: A通道={a_limit}, B通道={b_limit}")
            self.print_status()

            # 更新记录的上一次值
            last_a_limit = a_limit
            last_b_limit = b_limit

    def get_output_strength(self):
        """获取当前应该输出的强度，确保不超过手机上限"""
        if self.is_paused or self.protect_active or not self.output_active:
            return 1, 1

        # 确保强度不低于1且不超过手机上限
        output_a = max(1, min(self.current_strength_a, self.a_limit))
        output_b = max(1, min(self.current_strength_b, self.b_limit))

        return output_a, output_b

    def print_status(self):
        """打印当前状态信息"""
        global current_waveform_index_a, current_waveform_index_b
        print("=" * 30)
        print("|当前状态信息:")
        print(f"|A通道波形: {available_waveforms[current_waveform_index_a % len(available_waveforms)]}")
        print(f"|B通道波形: {available_waveforms[current_waveform_index_b % len(available_waveforms)]}")
        print(f"|A通道强度: {self.current_strength_a}/{self.a_limit}")
        print(f"|B通道强度: {self.current_strength_b}/{self.b_limit}")
        print("=" * 30)


async def send_waveform(channel, waveform_name=None, clear_first=True, print_info=True):
    """发送波形到指定通道"""
    global last_waveform_name_a, last_waveform_name_b

    try:
        if client is None:
            return False

        if waveform_name is None:
            if channel == Channel.A:
                waveform_name = available_waveforms[current_waveform_index_a % len(available_waveforms)]
            else:
                waveform_name = available_waveforms[current_waveform_index_b % len(available_waveforms)]

        if waveform_name in PULSE_DATA:
            # 检查波形是否发生变化，只有变化时才打印
            should_print = False
            if channel == Channel.A:
                if last_waveform_name_a != waveform_name:
                    last_waveform_name_a = waveform_name
                    should_print = True
            else:
                if last_waveform_name_b != waveform_name:
                    last_waveform_name_b = waveform_name
                    should_print = True

            # 只有在波形发生变化且需要打印信息时才打印
            if print_info and should_print:
                if channel == Channel.A:
                    print(f"发送波形到A通道: {waveform_name}")
                    simple_control.print_status()
                else:
                    print(f"发送波形到B通道: {waveform_name}")
                    simple_control.print_status()

            # 清除旧波形
            if clear_first:
                try:
                    await client.clear_pulses(channel)
                    await asyncio.sleep(0.1)
                except Exception as e:
                    pass  # 忽略清除波形错误

            pulse_data = PULSE_DATA[waveform_name]

            # 如果波形太长，截断到安全长度
            if len(pulse_data) > 500:
                pulse_data = pulse_data[:500]

            # 分块发送
            chunk_size = 100
            for i in range(0, len(pulse_data), chunk_size):
                chunk = pulse_data[i:i + chunk_size]
                await client.add_pulses(channel, *chunk)
                await asyncio.sleep(0.05)

            return True
        else:
            return False
    except Exception as e:
        return False  # 忽略发送波形错误


async def set_strength(channel, strength):
    """设置指定通道的强度，确保不超过手机上限"""
    global last_strength_a, last_strength_b

    try:
        if client and simple_control:
            # 获取当前通道的上限
            limit = simple_control.a_limit if channel == Channel.A else simple_control.b_limit

            # 检查强度是否超过上限
            if strength > limit:
                print(f"警告: {channel.name}通道强度{strength}超过手机上限{limit}，设置为上限值{limit}")
                strength = limit

            # 确保强度不低于1
            strength = max(1, strength)

            # 检查强度是否变化，变化时才打印
            should_print = False
            if channel == Channel.A and last_strength_a != strength:
                last_strength_a = strength
                should_print = True
            elif channel == Channel.B and last_strength_b != strength:
                last_strength_b = strength
                should_print = True

            if should_print:
                print(f"设置{channel.name}通道强度: {strength}/{limit}")
                simple_control.print_status()

            await client.set_strength(
                channel,
                StrengthOperationType.SET_TO,
                strength
            )
    except Exception as e:
        pass  # 忽略设置强度错误


async def control_loop():
    """主控制循环"""
    global simple_control

    simple_control = SimpleControl()
    waveform_counter = 0

    try:
        while True:
            # 获取当前输出强度（这里会确保不超过上限）
            output_strength_a, output_strength_b = simple_control.get_output_strength()

            # 分别设置A、B通道强度
            await set_strength(Channel.A, output_strength_a)
            await set_strength(Channel.B, output_strength_b)

            # 定期发送波形
            if waveform_counter % WAVEFORM_SEND_INTERVAL == 0:
                # 发送波形到两个通道，但不打印信息
                await send_waveform(Channel.A, clear_first=False, print_info=False)
                await send_waveform(Channel.B, clear_first=False, print_info=False)

            waveform_counter += 1
            await asyncio.sleep(0.1)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        pass  # 忽略控制循环错误


async def main():
    """主函数"""
    global client, control_task, current_waveform_index_a, current_waveform_index_b, simple_control

    try:
        print("=" * 50)
        print("DG-Lab 简化控制 Demo")
        print("功能说明:")
        print("1. A1按钮: 切换到下一个波形")
        print("2. A2按钮: A通道强度+1")
        print("3. A3按钮: A通道强度-1")
        print("4. B1按钮: 切换到下一个波形")
        print("5. B2按钮: B通道强度+1")
        print("6. B3按钮: B通道强度-1")
        print("=" * 50)
        print(f"初始A通道波形: {available_waveforms[current_waveform_index_a]}")
        print(f"初始B通道波形: {available_waveforms[current_waveform_index_b]}")

        # 连接到服务端
        try:
            async with DGLabWSConnect(f'ws://{get_host_ip()}:5678', CONNECTION_TIMEOUT) as ws_client:
                client = ws_client

                # 获取二维码
                url = client.get_qrcode()
                print("请用 DG-Lab App 扫描二维码以连接")
                print_qrcode(url)

                # 等待绑定
                await client.bind()
                print(f"已与 App {client.target_id} 成功绑定")

                # 显示初始状态信息
                simple_control = SimpleControl()
                simple_control.print_status()

                # 启动控制任务
                control_task = asyncio.create_task(control_loop())

                # 处理DG-Lab消息
                async for data in client.data_generator():

                    # 接收通道强度数据（获取手机上限）
                    if isinstance(data, StrengthData):
                        # 直接从手机数据获取上限
                        a_limit = data.a_limit
                        b_limit = data.b_limit

                        # 更新控制实例中的上限（只有变化时才打印）
                        if simple_control:
                            simple_control.update_limits(a_limit, b_limit)

                    # 接收 App 反馈按钮
                    elif isinstance(data, FeedbackButton):
                        print(f"按钮: {data.name}")

                        if data == FeedbackButton.A1:
                            # A1按钮：切换到下一个波形
                            current_waveform_index_a = (current_waveform_index_a + 1) % len(available_waveforms)
                            print(f"A通道切换到下一个波形: {available_waveforms[current_waveform_index_a]}")
                            await send_waveform(Channel.A)

                        elif data == FeedbackButton.A2:
                            # A2按钮：A通道强度+1，由set_strength函数处理上限
                            if simple_control:
                                simple_control.current_strength_a += 1

                        elif data == FeedbackButton.A3:
                            # A3按钮：A通道强度-1，确保不低于1
                            if simple_control:
                                new_strength = max(simple_control.current_strength_a - 1, 1)
                                simple_control.current_strength_a = new_strength

                        elif data == FeedbackButton.B1:
                            # B1按钮：切换到下一个波形
                            current_waveform_index_b = (current_waveform_index_b + 1) % len(available_waveforms)
                            print(f"B通道切换到下一个波形: {available_waveforms[current_waveform_index_b]}")
                            await send_waveform(Channel.B)

                        elif data == FeedbackButton.B2:
                            # B2按钮：B通道强度+1，由set_strength函数处理上限
                            if simple_control:
                                simple_control.current_strength_b += 1

                        elif data == FeedbackButton.B3:
                            # B3按钮：B通道强度-1，确保不低于1
                            if simple_control:
                                new_strength = max(simple_control.current_strength_b - 1, 1)
                                simple_control.current_strength_b = new_strength

                    # 接收心跳/App断开通知
                    elif data == RetCode.CLIENT_DISCONNECTED:
                        print("App 已断开连接，尝试重新绑定...")
                        await client.rebind()
                        print("重新绑定成功")

                        # 重新绑定后显示当前状态
                        if simple_control:
                            simple_control.print_status()

                # 取消控制任务
                control_task.cancel()
                await control_task

        except ConnectionRefusedError as e:
            print('连接服务器错误，请确保server.exe已启动')
            print('找到server.exe文件并双击运行，然后保持窗口打开')
        except Exception as e:
            print(f"连接错误: {e}")

    except Exception as e:
        print(f"程序错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 程序结束时将强度设置为最小值
        try:
            if client:
                await set_strength(Channel.A, 0)
                await set_strength(Channel.B, 0)
        except:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    except Exception as e:
        print(f"程序启动错误: {e}")
        import traceback
        traceback.print_exc()