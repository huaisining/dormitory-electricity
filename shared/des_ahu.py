# -*- coding: utf-8 -*-
""""AHU CAS DES encryption - EXACT Python port of com.yourname.ahu_plus.util.DES.kt
which itself is a port of https://one.ahu.edu.cn/cas/comm/js/des.js

Usage: DES.str_enc(username + password + lt, '1', '2', '3')
Returns hex string for CAS login form.
"""

class DES:
    S1 = [[14,4,13,1,2,15,11,8,3,10,6,12,5,9,0,7],
          [0,15,7,4,14,2,13,1,10,6,12,11,9,5,3,8],
          [4,1,14,8,13,6,2,11,15,12,9,7,3,10,5,0],
          [15,12,8,2,4,9,1,7,5,11,3,14,10,0,6,13]]
    S2 = [[15,1,8,14,6,11,3,4,9,7,2,13,12,0,5,10],
          [3,13,4,7,15,2,8,14,12,0,1,10,6,9,11,5],
          [0,14,7,11,10,4,13,1,5,8,12,6,9,3,2,15],
          [13,8,10,1,3,15,4,2,11,6,7,12,0,5,14,9]]
    S3 = [[10,0,9,14,6,3,15,5,1,13,12,7,11,4,2,8],
          [13,7,0,9,3,4,6,10,2,8,5,14,12,11,15,1],
          [13,6,4,9,8,15,3,0,11,1,2,12,5,10,14,7],
          [1,10,13,0,6,9,8,7,4,15,14,3,11,5,2,12]]
    S4 = [[7,13,14,3,0,6,9,10,1,2,8,5,11,12,4,15],
          [13,8,11,5,6,15,0,3,4,7,2,12,1,10,14,9],
          [10,6,9,0,12,11,7,13,15,1,3,14,5,2,8,4],
          [3,15,0,6,10,1,13,8,9,4,5,11,12,7,2,14]]
    S5 = [[2,12,4,1,7,10,11,6,8,5,3,15,13,0,14,9],
          [14,11,2,12,4,7,13,1,5,0,15,10,3,9,8,6],
          [4,2,1,11,10,13,7,8,15,9,12,5,6,3,0,14],
          [11,8,12,7,1,14,2,13,6,15,0,9,10,4,5,3]]
    S6 = [[12,1,10,15,9,2,6,8,0,13,3,4,14,7,5,11],
          [10,15,4,2,7,12,9,5,6,1,13,14,0,11,3,8],
          [9,14,15,5,2,8,12,3,7,0,4,10,1,13,11,6],
          [4,3,2,12,9,5,15,10,11,14,1,7,6,0,8,13]]
    S7 = [[4,11,2,14,15,0,8,13,3,12,9,7,5,10,6,1],
          [13,0,11,7,4,9,1,10,14,3,5,12,2,15,8,6],
          [1,4,11,13,12,3,7,14,10,15,6,8,0,5,9,2],
          [6,11,13,8,1,4,10,7,9,5,0,15,14,2,3,12]]
    S8 = [[13,2,8,4,6,15,11,1,10,9,3,14,5,0,12,7],
          [1,15,13,8,10,3,7,4,12,5,6,11,0,14,9,2],
          [7,11,4,1,9,12,14,2,0,6,10,13,15,3,5,8],
          [2,1,14,7,4,10,8,13,15,12,9,0,3,5,6,11]]
    S_BOXES = [S1, S2, S3, S4, S5, S6, S7, S8]

    LOOP = [1,1,2,2,2,2,2,2,1,2,2,2,2,2,2,1]

    @staticmethod
    def str_to_bt(s):
        bt = [0]*64
        leng = min(len(s), 4)
        for i in range(leng):
            k = ord(s[i])
            for j in range(16):
                bt[16*i + j] = (k >> (15 - j)) & 1
        return bt

    @staticmethod
    def bt64_to_hex(bt):
        hex_s = ""
        for i in range(16):
            val = 0
            for j in range(4):
                val = (val << 1) | bt[i*4 + j]
            hex_s += f"{val:X}"
        return hex_s

    @staticmethod
    def bt4_to_hex(binary):
        return {"0000":"0","0001":"1","0010":"2","0011":"3","0100":"4",
                "0101":"5","0110":"6","0111":"7","1000":"8","1001":"9",
                "1010":"A","1011":"B","1100":"C","1101":"D","1110":"E","1111":"F"}[binary]

    @staticmethod
    def get_box_binary(i):
        return ["0000","0001","0010","0011","0100","0101","0110","0111",
                "1000","1001","1010","1011","1100","1101","1110","1111"][i]

    @staticmethod
    def get_key_bytes(key):
        result = []
        for i in range(0, len(key), 4):
            chunk = key[i:i+4]
            result.append(DES.str_to_bt(chunk))
        return result

    @classmethod
    def init_permute(cls, data):
        ip = [0]*64
        m, n = 1, 0
        for i in range(4):
            for j in range(7, -1, -1):
                ip[i*8 + (7-j)] = data[j*8 + m]
                ip[i*8 + (7-j) + 32] = data[j*8 + n]
            m += 2
            n += 2
        return ip

    @classmethod
    def expand_permute(cls, right):
        ep = [0]*48
        for i in range(8):
            ep[i*6 + 0] = right[31] if i == 0 else right[i*4 - 1]
            ep[i*6 + 1] = right[i*4 + 0]
            ep[i*6 + 2] = right[i*4 + 1]
            ep[i*6 + 3] = right[i*4 + 2]
            ep[i*6 + 4] = right[i*4 + 3]
            ep[i*6 + 5] = right[0] if i == 7 else right[i*4 + 4]
        return ep

    @staticmethod
    def xor_(a, b):
        return [a[i] ^ b[i] for i in range(len(a))]

    @classmethod
    def s_box_permute(cls, expand):
        sb = [0]*32
        for m in range(8):
            chunk = expand[m*6:(m+1)*6]
            row = (chunk[0] << 1) | chunk[5]
            col = (chunk[1] << 3) | (chunk[2] << 2) | (chunk[3] << 1) | chunk[4]
            val = cls.S_BOXES[m][row][col]
            for k in range(4):
                sb[m*4 + k] = (val >> (3 - k)) & 1
        return sb

    @classmethod
    def p_permute(cls, sb):
        p = [0]*32
        p[0]=sb[15]; p[1]=sb[6]; p[2]=sb[19]; p[3]=sb[20]; p[4]=sb[28]; p[5]=sb[11]
        p[6]=sb[27]; p[7]=sb[16]; p[8]=sb[0]; p[9]=sb[14]; p[10]=sb[22]; p[11]=sb[25]
        p[12]=sb[4]; p[13]=sb[17]; p[14]=sb[30]; p[15]=sb[9]; p[16]=sb[1]; p[17]=sb[7]
        p[18]=sb[23]; p[19]=sb[13]; p[20]=sb[31]; p[21]=sb[26]; p[22]=sb[2]; p[23]=sb[8]
        p[24]=sb[18]; p[25]=sb[12]; p[26]=sb[29]; p[27]=sb[5]; p[28]=sb[21]; p[29]=sb[10]
        p[30]=sb[3]; p[31]=sb[24]
        return p

    @classmethod
    def finally_permute(cls, end):
        fp = [0]*64
        fp[0]=end[39]; fp[1]=end[7]; fp[2]=end[47]; fp[3]=end[15]; fp[4]=end[55]; fp[5]=end[23]
        fp[6]=end[63]; fp[7]=end[31]; fp[8]=end[38]; fp[9]=end[6]; fp[10]=end[46]; fp[11]=end[14]
        fp[12]=end[54]; fp[13]=end[22]; fp[14]=end[62]; fp[15]=end[30]; fp[16]=end[37]; fp[17]=end[5]
        fp[18]=end[45]; fp[19]=end[13]; fp[20]=end[53]; fp[21]=end[21]; fp[22]=end[61]; fp[23]=end[29]
        fp[24]=end[36]; fp[25]=end[4]; fp[26]=end[44]; fp[27]=end[12]; fp[28]=end[52]; fp[29]=end[20]
        fp[30]=end[60]; fp[31]=end[28]; fp[32]=end[35]; fp[33]=end[3]; fp[34]=end[43]; fp[35]=end[11]
        fp[36]=end[51]; fp[37]=end[19]; fp[38]=end[59]; fp[39]=end[27]; fp[40]=end[34]; fp[41]=end[2]
        fp[42]=end[42]; fp[43]=end[10]; fp[44]=end[50]; fp[45]=end[18]; fp[46]=end[58]; fp[47]=end[26]
        fp[48]=end[33]; fp[49]=end[1]; fp[50]=end[41]; fp[51]=end[9]; fp[52]=end[49]; fp[53]=end[17]
        fp[54]=end[57]; fp[55]=end[25]; fp[56]=end[32]; fp[57]=end[0]; fp[58]=end[40]; fp[59]=end[8]
        fp[60]=end[48]; fp[61]=end[16]; fp[62]=end[56]; fp[63]=end[24]
        return fp

    @classmethod
    def generate_keys(cls, key_bt):
        k = [0]*56
        for i in range(7):
            for j in range(8):
                k[i*8 + j] = key_bt[8*(7-j) + i]
        keys_arr = [[0]*48 for _ in range(16)]
        for i in range(16):
            for _ in range(cls.LOOP[i]):
                tl = k[0]; tr = k[28]
                for x in range(27): k[x]=k[x+1]; k[28+x]=k[29+x]
                k[27]=tl; k[55]=tr
            tk = [0]*48
            tk[0]=k[13]; tk[1]=k[16]; tk[2]=k[10]; tk[3]=k[23]; tk[4]=k[0]; tk[5]=k[4]
            tk[6]=k[2]; tk[7]=k[27]; tk[8]=k[14]; tk[9]=k[5]; tk[10]=k[20]; tk[11]=k[9]
            tk[12]=k[22]; tk[13]=k[18]; tk[14]=k[11]; tk[15]=k[3]; tk[16]=k[25]; tk[17]=k[7]
            tk[18]=k[15]; tk[19]=k[6]; tk[20]=k[26]; tk[21]=k[19]; tk[22]=k[12]; tk[23]=k[1]
            tk[24]=k[40]; tk[25]=k[51]; tk[26]=k[30]; tk[27]=k[36]; tk[28]=k[46]; tk[29]=k[54]
            tk[30]=k[29]; tk[31]=k[39]; tk[32]=k[50]; tk[33]=k[44]; tk[34]=k[32]; tk[35]=k[47]
            tk[36]=k[43]; tk[37]=k[48]; tk[38]=k[38]; tk[39]=k[55]; tk[40]=k[33]; tk[41]=k[52]
            tk[42]=k[45]; tk[43]=k[41]; tk[44]=k[49]; tk[45]=k[35]; tk[46]=k[28]; tk[47]=k[31]
            keys_arr[i] = tk
        return keys_arr

    @classmethod
    def enc(cls, data_bt, key_bt):
        keys = cls.generate_keys(key_bt)
        ip_bt = cls.init_permute(data_bt)
        ip_left = ip_bt[:32]
        ip_right = ip_bt[32:]
        for i in range(16):
            temp_left = ip_left[:]
            ip_left = ip_right[:]
            temp_right = cls.xor_(cls.p_permute(cls.s_box_permute(
                cls.xor_(cls.expand_permute(ip_right), keys[i]))), temp_left)
            ip_right = temp_right
        final = ip_right + ip_left
        return cls.finally_permute(final)

    @classmethod
    def str_enc(cls, data, first_key="1", second_key="2", third_key="3"):
        result = ""
        for i in range(0, len(data), 4):
            chunk = data[i:i+4]
            bt = cls.str_to_bt(chunk)
            for key_str in [first_key, second_key, third_key]:
                if key_str:
                    for key_bt in cls.get_key_bytes(key_str):
                        bt = cls.enc(bt, key_bt)
            result += cls.bt64_to_hex(bt)
        return result


if __name__ == "__main__":
    test = DES.str_enc("test", "1", "2", "3")
    print(f"DES.str_enc('test', '1', '2', '3') = {test}")
    print(f"Length: {len(test)}")
    # Try known AHU pattern
    t2 = DES.str_enc("abc123", "1", "2", "3")
    print(f"DES.str_enc('abc123') = {t2}")
