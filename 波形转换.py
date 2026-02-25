import re

def create_value_range(start_val, end_val, steps=4):
    """
    创建值范围，包含指定数量的值
    """
    if steps <= 1:
        return (start_val,)

    step_size = (end_val - start_val) / (steps - 1)
    values = tuple(int(round(start_val + i * step_size)) for i in range(steps))
    return values


def parse_waveform_data(data_string, range_steps=4):
    """
    解析波形数据字符串，处理所有section，最后添加一次休息时间
    """
    result = {
        "pulse_params": None,
        "rest_time": None,
        "rest_time_seconds": None,
        "frequency_groups": [],
        "intensity_lists": [],
        "loop_counts": [],
        "frequency_sequences": [],
        "combined_sequences": [],
        "final_sequence": []  # 最终组合序列
    }

    # 提取脉冲参数 pulse:18,1,8
    pulse_match = re.search(r'\+pulse:(\d+,\d+,\d+)', data_string)
    if pulse_match:
        result["pulse_params"] = pulse_match.group(1)
        rest_time_raw = int(pulse_match.group(1).split(',')[0])
        result["rest_time"] = rest_time_raw
        result["rest_time_seconds"] = round(rest_time_raw / 100.0, 1)

    # 使用section分割数据
    sections = re.split(r'\+section\+', data_string)

    # 处理每个section
    all_section_sequences = []

    for section in sections:
        # 提取频率参数组
        freq_match = re.search(r'(\d+,\d+,\d+,\d+,\d+)/', section)
        if not freq_match:
            continue

        freq_params = [int(x) for x in freq_match.group(1).split(',')]
        result["frequency_groups"].append(freq_params)

        # 提取强度数据
        intensity_pattern = r'(\d+\.\d+)-'
        intensity_matches = re.findall(intensity_pattern, section)
        intensities = [int(float(x)) for x in intensity_matches]
        result["intensity_lists"].append(intensities)

        # 计算循环次数
        expected_seconds = (freq_params[2] + 1) / 10.0
        group_duration = len(intensities) * 0.1
        loop_count = int((expected_seconds + group_duration - 0.0001) // group_duration)
        result["loop_counts"].append(loop_count)

        # 计算频率
        start_freq = freq_params[0] + 10
        end_freq = freq_params[1] + 10
        change_type = freq_params[3]

        frequency_sequence = []
        combined_sequence = []

        if change_type == 1 or 4:  # 固定频率
            fixed_freq = start_freq
            total_points = len(intensities) * loop_count

            frequency_sequence = [fixed_freq] * total_points

            for i in range(total_points):
                current_intensity = intensities[i % len(intensities)]
                next_intensity = intensities[(i + 1) % len(intensities)] if i < total_points - 1 else intensities[0]

                intensity_range = create_value_range(current_intensity, next_intensity, range_steps)
                freq_range = (fixed_freq,) * range_steps

                combined_sequence.append((freq_range, intensity_range))

        elif change_type == 2:  # 节内循环
            for loop_idx in range(loop_count):
                for i in range(len(intensities)):
                    progress = i / (len(intensities) - 1) if len(intensities) > 1 else 0
                    current_freq = start_freq + (end_freq - start_freq) * progress
                    freq_val = int(round(current_freq))
                    frequency_sequence.append(freq_val)

            for i in range(len(frequency_sequence)):
                current_freq_val = frequency_sequence[i]
                current_intensity = intensities[i % len(intensities)]
                next_intensity = intensities[(i + 1) % len(intensities)] if i < len(frequency_sequence) - 1 else intensities[0]

                intensity_range = create_value_range(current_intensity, next_intensity, range_steps)
                next_freq = frequency_sequence[(i + 1) % len(frequency_sequence)] if i < len(frequency_sequence) - 1 else frequency_sequence[0]
                freq_range = create_value_range(current_freq_val, next_freq, range_steps)

                combined_sequence.append((freq_range, intensity_range))

        elif change_type == 3:  # 元内循环
            total_points = len(intensities) * loop_count

            for i in range(total_points):
                progress = i / (total_points - 1) if total_points > 1 else 0
                current_freq = start_freq + (end_freq - start_freq) * progress
                freq_val = int(round(current_freq))
                frequency_sequence.append(freq_val)

            for i in range(total_points):
                current_freq_val = frequency_sequence[i]
                current_intensity = intensities[i % len(intensities)]
                next_intensity = intensities[(i + 1) % len(intensities)] if i < total_points - 1 else intensities[0]

                intensity_range = create_value_range(current_intensity, next_intensity, range_steps)
                next_freq = frequency_sequence[(i + 1) % len(frequency_sequence)] if i < total_points - 1 else frequency_sequence[0]
                freq_range = create_value_range(current_freq_val, next_freq, range_steps)

                combined_sequence.append((freq_range, intensity_range))

        result["frequency_sequences"].append(frequency_sequence)
        result["combined_sequences"].append(combined_sequence)
        all_section_sequences.extend(combined_sequence)

    # 在所有section处理后，最后添加一次休息时间
    rest_time_points = 2  # 固定2个点
    rest_freq = 0  # 休息时频率为0
    rest_intensity = 0  # 休息时强度为0

    rest_sequence = []
    for i in range(rest_time_points):
        freq_range = (rest_freq,) * range_steps
        intensity_range = (rest_intensity,) * range_steps
        rest_sequence.append((freq_range, intensity_range))

    # 组合：所有section点 + 休息点
    final_sequence = all_section_sequences + rest_sequence
    result["final_sequence"] = final_sequence

    return result


# 测试数据
print('新版数据示例：Dungeonlab+pulse:0,1,8=0,11,16,1,1/70.00-1,80.00-0,90.00-0,100.00-1,100.00-1,88.33-0,76.67-0,65.00-1,100.00-1,86.67-0,73.33-0,60.00-1,73.33-0,86.67-0,100.00-1,85.00-1,92.50-0,100.00-1+section+0,0,22,1,1/60.00-1,61.25-0,62.50-0,63.75-0,65.00-1,73.75-0,82.50-0,91.25-0,100.00-1+section+0,20,10,1,1/100.00-1,100.00-0,100.00-0,100.00-0,100.00-0,100.00-0,100.00-0,100.00-0,100.00-0,100.00-0,100.00-0,100.00-1')
test_data = input('请完整输入新版数据：')
# 执行解析
parsed_result = parse_waveform_data(data_string=test_data, range_steps=4)

# 输出完整最终序列
print("完整最终序列:")
print("[")
for i, item in enumerate(parsed_result["final_sequence"]):
    if i < len(parsed_result["final_sequence"]) - 1:
        print(f"    {item},")
    else:
        print(f"    {item}")
print("]")

# 同时输出其他信息
print(f"\n其他信息:")
print(f"脉冲参数: {parsed_result['pulse_params']}")
print(f"休息时间: {parsed_result['rest_time']} -> {parsed_result['rest_time_seconds']}s")
print(f"总点数: {len(parsed_result['final_sequence'])}")