"""
许可证签发工具（卖家侧，配套 license_core.py 的验证逻辑）
================================================================
license_core.py 只实现了 ECDSA secp256k1 的【验证】半边，本工具补齐【签名】半边，
用于卖家离线签发许可证。生成的许可证字符串与 license_core.decode_and_validate()
严格兼容（同一二进制格式、同一签名消息、同一验签算法）。

用法示例:
    # 给定私钥 d 与机器码，签发 90 天许可证
    python gen_license.py --machine-id <32hex> --customer 张三 --days 90 --key <私钥d的十进制或hex>

安全须知:
    - 私钥 d 仅卖家本人持有，本脚本从未把私钥写入任何文件。
    - 不要将本脚本与私钥一起分发给客户（客户机器上只有验证半边 license_core.py）。
"""
import argparse
import base64
import hashlib
import hmac
import os
import struct
import sys
import time

# 复用 license_core 的曲线参数 / 曲线运算 / 公钥 / 验签逻辑，
# 保证签发端与验证端实现完全一致（同一套 elliptic-curve 代码，避免偏差）。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import license_core as lc


# ============================================================
# ECDSA 签名（基于 license_core 已有的曲线运算，形成完整闭环）
# ============================================================

def _int_to_bytes(n: int) -> bytes:
    """非负整数转 32 字节大端字节串"""
    return n.to_bytes(32, "big")


def _ecdsa_sign(d: int, msg_hash_int: int) -> tuple:
    """
    生成 ECDSA 签名的 (r, s)。
    使用 RFC6979 确定性 nonce（HMAC-SHA256，private key + message hash），
    避免随机数复用导致的私钥泄露风险。
    """
    while True:
        # ---- RFC6979: 由私钥与消息哈希确定性派生 nonce k ----
        k = _rfc6979_nonce(d, msg_hash_int)

        # ---- 经典 ECDSA 签名公式 ----
        point = lc._scalar_mult(k, lc._G)
        if point is None:
            continue
        r = point[0] % lc._N
        if r == 0:
            continue
        s = (pow(k, lc._N - 2, lc._N) * (msg_hash_int + r * d)) % lc._N
        if s == 0:
            continue
        # 若 s > N/2 则取 N - s（低 s 值，与多数库默认行为一致，提高兼容性）
        if s > lc._N // 2:
            s = lc._N - s
        return r, s


def _rfc6979_nonce(d: int, msg_hash_int: int) -> int:
    """
    RFC6979 确定性 nonce 生成。
    x = 私钥 d (32字节)，h1 = 消息哈希 (32字节)。
    """
    # qlen/belen 均为曲线阶 N 的位长与字节长（secp256k1 为 256/32）
    x = _int_to_bytes(d)
    h1 = _int_to_bytes(msg_hash_int)

    b = 32  # 输出字节长
    V = b"\x01" * b
    K = b"\x00" * b

    # Step b: K = HMAC_K(V || 0x00 || int2octets(x) || bits2octets(h1))
    bits2octets = _bits2octets(msg_hash_int)
    K = _hmac(K, V + b"\x00" + x + bits2octets)
    # Step c: V = HMAC_K(V)
    V = _hmac(K, V)
    # Step d: K = HMAC_K(V || 0x01 || int2octets(x) || bits2octets(h1))
    K = _hmac(K, V + b"\x01" + x + bits2octets)
    # Step e: V = HMAC_K(V)
    V = _hmac(K, V)

    # 循环生成候选 k
    while True:
        T = b""
        while len(T) < b:
            V = _hmac(K, V)
            T += V
        k = int.from_bytes(T[:b], "big")
        if 1 <= k < lc._N:
            return k
        # k 越界时继续：K = HMAC_K(V || 0x00)；V = HMAC_K(V)
        K = _hmac(K, V + b"\x00")
        V = _hmac(K, V)


def _hmac(key: bytes, msg: bytes) -> bytes:
    """HMAC-SHA256"""
    return hmac.new(key, msg, hashlib.sha256).digest()


def _bits2octets(h1_int: int) -> bytes:
    """
    RFC6979 的 bits2octets 变换: z = h1 对 q 取模，再转 32 字节。
    """
    z1 = h1_int % lc._N
    return _int_to_bytes(z1)


# ============================================================
# 许可证字符串构造（与 license_core.decode_and_validate 严格一致）
# ============================================================

def gen_license(priv_key: int, machine_id: str, customer: str, expiry_ts: int) -> str:
    """
    构造并签名一份许可证字符串。

    参数:
        priv_key: 卖家 ECDSA 私钥 d（十进制 int 或 hex）
        machine_id: 32 位十六进制机器码（license_core.get_machine_id() 的结果）
        customer: 客户名称
        expiry_ts: 到期 Unix 时间戳

    返回:
        base64 编码的许可证字符串（与验证端完全兼容）

    二进制格式（与 license_core 保持一致）:
        [1B 版本=0x02][4B 到期 uint32 BE][1B 客户名长度][N B 客户名 UTF-8][32B r][32B s]
        签名消息 = machine_id_bytes(16) + expiry_bytes(4) + customer_bytes(N)
        备注: machine_id 为 32 hex(16字节)，作为原字节串参与签名。
    """
    # ---- 组装负载字段 ----
    version = lc._VER  # 0x02
    expiry = int(expiry_ts)
    cust_bytes = customer.encode("utf-8")
    if len(cust_bytes) > 255:
        raise ValueError("客户名称过长（最多 255 字节 UTF-8）")
    if expiry <= 0 or expiry > 0xFFFFFFFF:
        raise ValueError("到期时间戳超出 4 字节范围")

    # 机器码处理：license_core 使用 get_machine_id() 的 32 hex（16 字节）
    try:
        mid_bytes = bytes.fromhex(machine_id.strip())
    except ValueError:
        raise ValueError(f"机器码不是合法的 32 位十六进制: {machine_id!r}")
    if len(mid_bytes) != 16:
        raise ValueError(f"机器码必须是 32 位十六进制（16 字节），当前 {len(mid_bytes)} 字节")

    # ---- 构造签名消息并与验证端完全一致的哈希 ----
    msg = mid_bytes + struct.pack(">I", expiry) + cust_bytes
    msg_hash = int.from_bytes(hashlib.sha256(msg).digest(), "big")

    # ---- 校验公钥与私钥匹配（防止输错私钥签发无法验证的许可证）----
    pub_point = lc._scalar_mult(priv_key, lc._G)
    if pub_point != lc._Q:
        raise ValueError(
            "私钥与源码内置的公钥 _Q 不匹配，签出的许可证将无法通过验证"
        )

    # ---- 签名 ----
    r, s = _ecdsa_sign(priv_key, msg_hash)

    # ---- 组装二进制 ----
    payload = (
        bytes([version])
        + struct.pack(">I", expiry)
        + bytes([len(cust_bytes)])
        + cust_bytes
        + _int_to_bytes(r)
        + _int_to_bytes(s)
    )
    return base64.b64encode(payload).decode("ascii")


# ============================================================
# 命令行入口
# ============================================================

def _parse_key(text: str) -> int:
    """解析私钥：支持十进制或 0x 十六进制"""
    text = text.strip()
    try:
        return int(text, 0) if text.lower().startswith("0x") else int(text, 10)
    except ValueError:
        raise ValueError("私钥必须是十进制整数或 0x 开头的十六进制")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="签发配套的 secp256k1 ECDSA 许可证（与 license_core.py 验证逻辑完全兼容）"
    )
    parser.add_argument("--key", required=True, help="卖家私钥 d（十进制或 0x 十六进制）")
    parser.add_argument("--machine-id", required=True,
                        help="目标机器码（32 位十六进制，如 3f7434af-5e5728e8...）")
    parser.add_argument("--customer", required=True, help="客户名称")
    parser.add_argument("--days", type=int, default=365, help="有效天数（默认 365）")
    parser.add_argument("--expiry", type=int, default=None,
                        help="手动指定到期 Unix 时间戳（优先于 --days）")
    parser.add_argument("--out", default=None, help="可选：写入许可证到文件")
    args = parser.parse_args()

    try:
        priv_key = _parse_key(args.key)
        machine_id = args.machine_id.replace("-", "").replace(" ", "")

        if args.expiry is not None:
            expiry_ts = args.expiry
        else:
            expiry_ts = int(time.time()) + int(args.days * 86400)

        license_str = gen_license(priv_key, machine_id, args.customer, expiry_ts)

        # ---- 自校验：用 license_core 的验证函数回测，确保能通过 ----
        val = lc.decode_and_validate(license_str, machine_id)
        if not val["valid"]:
            print(f"[错误] 自校验失败: {val['reason']}", file=sys.stderr)
            return 1

        print("=== 许可证签发成功 ===")
        print(f"  客户:    {args.customer}")
        print(f"  机器码:  {args.machine_id}")
        print(f"  到期:    {time.strftime('%Y-%m-%d', time.localtime(expiry_ts))}")
        print(f"  天数:    {int((expiry_ts - time.time()) / 86400)}")
        print(f"  自校验:  通过")
        print("  --- 许可证字符串 ---")
        print(license_str)

        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(license_str)
            print(f"\n已写入: {args.out}")
        return 0

    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
