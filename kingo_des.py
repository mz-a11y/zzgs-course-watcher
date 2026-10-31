# -*- coding: utf-8 -*-
"""KINGOSOFT 教务系统专用 DES (strEnc) 的纯 Python 移植。

来源：/jw/custom/js/jkingo.des.js
用途：生成选课提交时的加密参数（getEncParams）。
仅实现单密钥加密路径（strEnc(data, key, null, null)），足够选课提交使用。
"""

# ---------- 基本工具 ----------
def str_to_bt(s):
    """把长度<=4的字符串转成64位bit数组（每字符16bit，大端；不足补0）。"""
    bt = [0] * 64
    leng = len(s)
    if leng < 4:
        for i in range(leng):
            k = ord(s[i])
            for j in range(16):
                bt[16 * i + j] = (k >> (15 - j)) & 1
    else:
        for i in range(4):
            k = ord(s[i])
            for j in range(16):
                bt[16 * i + j] = (k >> (15 - j)) & 1
    return bt


def get_key_bytes(key):
    """密钥字符串 -> 64bit块数组（每4字符一块）。"""
    key_bytes = []
    leng = len(key)
    iterator = leng // 4
    remainder = leng % 4
    for i in range(iterator):
        key_bytes.append(str_to_bt(key[i * 4:i * 4 + 4]))
    if remainder > 0:
        key_bytes.append(str_to_bt(key[iterator * 4:leng]))
    return key_bytes


BT4_HEX = ["0", "1", "2", "3", "4", "5", "6", "7",
           "8", "9", "A", "B", "C", "D", "E", "F"]


def bt4_to_hex(binary):
    return BT4_HEX[int(binary, 2)]


def bt64_to_hex(byte_data):
    hex_ = ""
    for i in range(16):
        bt = "".join(str(byte_data[i * 4 + j]) for j in range(4))
        hex_ += bt4_to_hex(bt)
    return hex_


def init_permute(original_data):
    ip_byte = [0] * 64
    m, n = 1, 0
    for i in range(4):
        k = 0
        for j in range(7, -1, -1):
            ip_byte[i * 8 + k] = original_data[j * 8 + m]
            ip_byte[i * 8 + k + 32] = original_data[j * 8 + n]
            k += 1
        m += 2
        n += 2
    return ip_byte


def expand_permute(right_data):
    ep_byte = [0] * 48
    for i in range(8):
        if i == 0:
            ep_byte[0] = right_data[31]
        else:
            ep_byte[i * 6] = right_data[i * 4 - 1]
        ep_byte[i * 6 + 1] = right_data[i * 4]
        ep_byte[i * 6 + 2] = right_data[i * 4 + 1]
        ep_byte[i * 6 + 3] = right_data[i * 4 + 2]
        ep_byte[i * 6 + 4] = right_data[i * 4 + 3]
        if i == 7:
            ep_byte[i * 6 + 5] = right_data[0]
        else:
            ep_byte[i * 6 + 5] = right_data[i * 4 + 4]
    return ep_byte


def xor(byte_one, byte_two):
    return [a ^ b for a, b in zip(byte_one, byte_two)]


S1 = [[14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7],
      [0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8],
      [4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0],
      [15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13]]
S2 = [[15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10],
      [3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5],
      [0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15],
      [13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9]]
S3 = [[10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8],
      [13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1],
      [13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7],
      [1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12]]
S4 = [[7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15],
      [13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9],
      [10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4],
      [3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14]]
S5 = [[2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9],
      [14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6],
      [4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14],
      [11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3]]
S6 = [[12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11],
      [10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8],
      [9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6],
      [4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13]]
S7 = [[4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1],
      [13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6],
      [1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2],
      [6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12]]
S8 = [[13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7],
      [1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2],
      [7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8],
      [2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11]]

SBOXES = [S1, S2, S3, S4, S5, S6, S7, S8]


def s_box_permute(expand_byte):
    s_box_byte = [0] * 32
    for m in range(8):
        i = expand_byte[m * 6] * 2 + expand_byte[m * 6 + 5]
        j = (expand_byte[m * 6 + 1] * 8 + expand_byte[m * 6 + 2] * 4
             + expand_byte[m * 6 + 3] * 2 + expand_byte[m * 6 + 4])
        val = SBOXES[m][i][j]
        for bit in range(4):
            s_box_byte[m * 4 + bit] = (val >> (3 - bit)) & 1
    return s_box_byte


P = [15, 6, 19, 20, 28, 11, 27, 16, 0, 14, 22, 25, 4, 17, 30, 9,
     1, 7, 23, 13, 31, 26, 2, 8, 18, 12, 29, 5, 21, 10, 3, 24]


def p_permute(s_box_byte):
    return [s_box_byte[i] for i in P]


FP = [39, 7, 47, 15, 55, 23, 63, 31, 38, 6, 46, 14, 54, 22, 62, 30,
      37, 5, 45, 13, 53, 21, 61, 29, 36, 4, 44, 12, 52, 20, 60, 28,
      35, 3, 43, 11, 51, 19, 59, 27, 34, 2, 42, 10, 50, 18, 58, 26,
      33, 1, 41, 9, 49, 17, 57, 25, 32, 0, 40, 8, 48, 16, 56, 24]


def finally_permute(end_byte):
    return [end_byte[i] for i in FP]


def generate_keys(key_byte):
    keys = [[0] * 48 for _ in range(16)]
    key = [0] * 56
    loop = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]
    for i in range(7):
        for j in range(8):
            k = 7 - j
            key[i * 8 + j] = key_byte[8 * k + i]
    IDX = [13, 16, 10, 23, 0, 4, 2, 27, 14, 5, 20, 9, 22, 18, 11, 3,
           25, 7, 15, 6, 26, 19, 12, 1, 40, 51, 30, 36, 46, 54, 29, 39,
           50, 44, 32, 47, 43, 48, 38, 55, 33, 52, 45, 41, 49, 35, 28, 31]
    for i in range(16):
        for _ in range(loop[i]):
            temp_left = key[0]
            temp_right = key[28]
            for k in range(27):
                key[k] = key[k + 1]
                key[28 + k] = key[29 + k]
            key[27] = temp_left
            key[55] = temp_right
        for m in range(48):
            keys[i][m] = key[IDX[m]]
    return keys


def enc(data_byte, key_byte):
    keys = generate_keys(key_byte)
    ip_byte = init_permute(data_byte)
    ip_left = ip_byte[:32]
    ip_right = ip_byte[32:]
    for i in range(16):
        temp_left = ip_left[:]
        ip_left = ip_right[:]
        temp_right = xor(p_permute(s_box_permute(xor(expand_permute(ip_right), keys[i]))), temp_left)
        ip_right = temp_right
    return finally_permute(ip_right + ip_left)


def str_enc(data, first_key, second_key=None, third_key=None):
    """仅实现单密钥路径（strEnc(data, key, null, null)）。"""
    leng = len(data)
    enc_data = ""
    first_key_bt = get_key_bytes(first_key) if first_key else None
    first_length = len(first_key_bt) if first_key_bt else 0

    if leng > 0:
        if leng < 4:
            bt = str_to_bt(data)
            temp_bt = bt
            for x in range(first_length):
                temp_bt = enc(temp_bt, first_key_bt[x])
            enc_data = bt64_to_hex(temp_bt)
        else:
            iterator = leng // 4
            remainder = leng % 4
            for i in range(iterator):
                temp_bt = str_to_bt(data[i * 4:i * 4 + 4])
                for x in range(first_length):
                    temp_bt = enc(temp_bt, first_key_bt[x])
                enc_data += bt64_to_hex(temp_bt)
            if remainder > 0:
                temp_bt = str_to_bt(data[iterator * 4:leng])
                for x in range(first_length):
                    temp_bt = enc(temp_bt, first_key_bt[x])
                enc_data += bt64_to_hex(temp_bt)
    return enc_data


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        print(str_enc(sys.argv[1], sys.argv[2]))
    else:
        # 自测样例（用 Node 跑 jkingo_des.js 交叉验证）
        print(str_enc("xh=201800019714&xn=2026", "testkey12345678"))
