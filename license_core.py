"""
软件许可证核心模块
- 机器指纹生成（硬件绑定）
- ECDSA secp256k1 数字签名验证（纯Python标准库，无第三方依赖）
- 许可证文件读写与校验

安全设计：
- 使用ECDSA secp256k1数字签名，私钥仅卖家持有
- 即使读取源码，也无法伪造许可证（没有私钥）
- 机器指纹绑定CPU+主板+BIOS，换机即失效
- 紧凑二进制格式，许可证仅约110字符
"""
import base64
import hashlib
import os
import platform
import struct
import subprocess
import time
import uuid

# ============================================================
# secp256k1 椭圆曲线参数
# ============================================================
_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
_GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8
_G = (_GX, _GY)

# ECDSA公钥 Q = d * G（由卖家私钥生成，可公开）
_QX = 0xeed4e2aca39e010f96ad4bd9be310e967b98317edd4b6033cd9eff3c7b5bfd75
_QY = 0xdcae11296500b408ba9f177414a98abf1085fd921c4078ee462d9a3d0d8f7bbe
_Q = (_QX, _QY)

# 许可证格式版本
_VER = 0x02


# ============================================================
# secp256k1 椭圆曲线运算（纯Python标准库）
# ============================================================

def _point_add(P1, P2):
    """椭圆曲线点加法 (secp256k1)"""
    if P1 is None:
        return P2
    if P2 is None:
        return P1
    x1, y1 = P1
    x2, y2 = P2
    if x1 == x2 and (y1 + y2) % _P == 0:
        return None
    if x1 == x2 and y1 == y2:
        m = (3 * x1 * x1) * pow(2 * y1, _P - 2, _P) % _P
    else:
        m = (y2 - y1) * pow((x2 - x1) % _P, _P - 2, _P) % _P
    x3 = (m * m - x1 - x2) % _P
    y3 = (m * (x1 - x3) - y1) % _P
    return (x3, y3)


def _scalar_mult(k, point):
    """椭圆曲线标量乘法 (double-and-add)"""
    result = None
    addend = point
    while k:
        if k & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        k >>= 1
    return result


def _ecdsa_verify(msg_hash_int, r, s):
    """验证 ECDSA 签名"""
    if not (1 <= r < _N and 1 <= s < _N):
        return False
    w = pow(s, _N - 2, _N)
    u1 = (msg_hash_int * w) % _N
    u2 = (r * w) % _N
    point = _point_add(_scalar_mult(u1, _G), _scalar_mult(u2, _Q))
    if point is None:
        return False
    return (point[0] % _N) == r


# ============================================================
# 机器指纹
# ============================================================

_machine_id_cache = None
_machine_id_legacy_cache = None


def _read_registry(hive, path, key):
    """读取Windows注册表值"""
    try:
        import winreg
        reg = winreg.OpenKey(hive, path)
        val, _ = winreg.QueryValueEx(reg, key)
        winreg.CloseKey(reg)
        return str(val)
    except Exception:
        return ""


def _powershell_get(cmd):
    """通过PowerShell获取硬件信息（替代已弃用的wmic）"""
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", cmd],
            timeout=10, stderr=subprocess.DEVNULL
        ).decode("utf-8", errors="ignore").strip()
        return out or ""
    except Exception:
        return ""


def _wmic_get(cmd):
    """通过wmic获取硬件信息（旧系统兼容）"""
    try:
        out = subprocess.check_output(
            cmd, shell=True, timeout=5,
            stderr=subprocess.DEVNULL
        ).decode("gbk", errors="ignore").strip()
        lines = [l.strip() for l in out.split("\n") if l.strip()]
        if len(lines) >= 2:
            return lines[-1]
        return ""
    except Exception:
        return ""


def get_machine_id() -> str:
    """
    生成机器唯一指纹（v3 稳定版）

    v3改进：
    - 移除uuid.getnode()（MAC地址）：多网卡/VPN/虚拟网卡会导致不固定
    - 添加注册表MachineGuid：Windows安装时生成，极稳定
    - wmic替换为PowerShell CIM：Windows 11 24H2已移除wmic
    - 添加缓存：避免每次调用都执行subprocess
    - 失败时用空字符串而非"error"

    返回32位十六进制字符串
    """
    global _machine_id_cache
    if _machine_id_cache is not None:
        return _machine_id_cache

    parts = []
    system = platform.system()

    if system == "Windows":
        # 1. 注册表 MachineGuid（极稳定，Windows安装时生成）
        try:
            import winreg
            parts.append(_read_registry(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
                "MachineGuid"
            ))
        except ImportError:
            parts.append("")

        # 2. CPU序列号（PowerShell CIM优先，wmic回退）
        cpu = _powershell_get("(Get-CimInstance Win32_Processor).ProcessorId")
        if not cpu:
            cpu = _wmic_get('wmic cpu get ProcessorId')
        parts.append(cpu)

        # 3. 主板序列号
        board = _powershell_get("(Get-CimInstance Win32_BaseBoard).SerialNumber")
        if not board:
            board = _wmic_get('wmic baseboard get SerialNumber')
        parts.append(board)

        # 4. BIOS序列号
        bios = _powershell_get("(Get-CimInstance Win32_BIOS).SerialNumber")
        if not bios:
            bios = _wmic_get('wmic bios get SerialNumber')
        parts.append(bios)
    else:
        for cmd_str in [
            "cat /proc/cpuinfo | grep -i 'serial' | head -1",
            "dmidecode -s baseboard-serial-number 2>/dev/null",
            "ioreg -l | grep IOPlatformSerialNumber 2>/dev/null | head -1",
        ]:
            try:
                out = subprocess.check_output(
                    cmd_str, shell=True, timeout=5,
                    stderr=subprocess.DEVNULL
                ).decode("utf-8", errors="ignore").strip()
                parts.append(out or "")
            except Exception:
                parts.append("")

    # 过滤空值，只用非空部分生成指纹
    non_empty = [p for p in parts if p]
    if not non_empty:
        non_empty = [platform.node()]

    combined = "|".join(non_empty)
    _machine_id_cache = hashlib.sha256(combined.encode("utf-8")).hexdigest()[:32]
    return _machine_id_cache


def get_machine_id_legacy() -> str:
    """
    旧版机器指纹（含MAC地址），用于向后兼容已激活的许可证。
    """
    global _machine_id_legacy_cache
    if _machine_id_legacy_cache is not None:
        return _machine_id_legacy_cache

    parts = []
    system = platform.system()

    if system == "Windows":
        wmic_cmds = [
            'wmic cpu get ProcessorId',
            'wmic baseboard get SerialNumber',
            'wmic bios get SerialNumber',
        ]
        for cmd in wmic_cmds:
            try:
                out = subprocess.check_output(
                    cmd, shell=True, timeout=5,
                    stderr=subprocess.DEVNULL
                ).decode("gbk", errors="ignore").strip()
                lines = [l.strip() for l in out.split("\n") if l.strip()]
                if len(lines) >= 2:
                    parts.append(lines[-1])
                else:
                    parts.append("unknown")
            except Exception:
                parts.append("error")
    else:
        for cmd_str in [
            "cat /proc/cpuinfo | grep -i 'serial' | head -1",
            "dmidecode -s baseboard-serial-number 2>/dev/null",
            "ioreg -l | grep IOPlatformSerialNumber 2>/dev/null | head -1",
        ]:
            try:
                out = subprocess.check_output(
                    cmd_str, shell=True, timeout=5,
                    stderr=subprocess.DEVNULL
                ).decode("utf-8", errors="ignore").strip()
                parts.append(out or "unknown")
            except Exception:
                parts.append("error")

    parts.append(str(uuid.getnode()))
    combined = "|".join(parts)
    _machine_id_legacy_cache = hashlib.sha256(combined.encode("utf-8")).hexdigest()[:32]
    return _machine_id_legacy_cache


def get_machine_id_short() -> str:
    """返回可读性更好的机器ID（分4段）"""
    mid = get_machine_id()
    return "-".join(mid[i:i+8] for i in range(0, 32, 8))


# ============================================================
# 许可证编解码（紧凑二进制格式 v2）
# ============================================================
# 二进制格式:
#   [1字节 版本=0x02]
#   [4字节 到期时间戳 uint32 BE]
#   [1字节 客户名长度]
#   [N字节 客户名 UTF-8]
#   [32字节 签名 r]
#   [32字节 签名 s]
#
# 签名消息: machine_id_bytes(16) + expiry_bytes(4) + customer_bytes(N)

def decode_and_validate(license_str: str, expected_machine_id: str) -> dict:
    """
    解码并验证许可证字符串
    返回: {"valid": bool, "reason": str, "expiry": int, "customer": str}
    """
    result = {"valid": False, "reason": "", "expiry": 0, "customer": ""}

    try:
        raw = base64.b64decode(license_str.strip())
    except Exception:
        result["reason"] = "许可证格式错误，无法解析"
        return result

    if len(raw) < 70:
        result["reason"] = "许可证数据长度不足"
        return result

    version = raw[0]
    if version != _VER:
        result["reason"] = f"许可证版本不支持（v{version}）"
        return result

    expiry = struct.unpack(">I", raw[1:5])[0]
    cust_len = raw[5]
    cust_bytes = raw[6:6 + cust_len]
    try:
        customer = cust_bytes.decode("utf-8")
    except Exception:
        result["reason"] = "客户名称解码失败"
        return result

    sig_offset = 6 + cust_len
    if len(raw) < sig_offset + 64:
        result["reason"] = "签名数据不完整"
        return result

    r = int.from_bytes(raw[sig_offset:sig_offset + 32], "big")
    s = int.from_bytes(raw[sig_offset + 32:sig_offset + 64], "big")

    # 构建签名消息: machine_id_bytes + expiry_bytes + customer_bytes
    mid_bytes = bytes.fromhex(expected_machine_id)
    msg = mid_bytes + struct.pack(">I", expiry) + cust_bytes
    msg_hash = int.from_bytes(hashlib.sha256(msg).digest(), "big")

    if not _ecdsa_verify(msg_hash, r, s):
        result["reason"] = "许可证签名验证失败（可能被篡改）"
        return result

    if expiry < time.time():
        result["reason"] = f"许可证已过期（到期时间: {time.strftime('%Y-%m-%d', time.localtime(expiry))}）"
        return result

    result["valid"] = True
    result["expiry"] = expiry
    result["customer"] = customer
    return result


# ============================================================
# 许可证文件管理
# ============================================================

def get_license_path(data_dir: str) -> str:
    """许可证文件路径"""
    return os.path.join(data_dir, "license.dat")


def save_license(data_dir: str, license_str: str) -> bool:
    """保存许可证字符串到文件"""
    try:
        with open(get_license_path(data_dir), "w", encoding="utf-8") as f:
            f.write(license_str.strip())
        return True
    except Exception:
        return False


def check_license(data_dir: str) -> dict:
    """
    检查当前机器的许可证状态
    返回: {"valid": bool, "reason": str, "expiry": int, "customer": str, "machine_id": str}

    向后兼容：先尝试新机器码（v3，无MAC地址），失败后尝试旧机器码（含MAC地址）。
    """
    machine_id = get_machine_id()
    result = {
        "valid": False, "reason": "",
        "expiry": 0, "customer": "",
        "machine_id": machine_id,
        "machine_id_short": get_machine_id_short(),
    }

    path = get_license_path(data_dir)
    if not os.path.exists(path):
        result["reason"] = "未找到许可证文件，请先激活"
        return result

    try:
        with open(path, "r", encoding="utf-8") as f:
            license_str = f.read().strip()
    except Exception:
        result["reason"] = "许可证文件读取失败"
        return result

    if not license_str:
        result["reason"] = "许可证文件为空"
        return result

    # 先用新机器码验证（v3，无MAC地址，稳定）
    val_result = decode_and_validate(license_str, machine_id)
    if val_result["valid"]:
        result["valid"] = True
        result["reason"] = val_result["reason"]
        result["expiry"] = val_result["expiry"]
        result["customer"] = val_result["customer"]
        return result

    # 新机器码验证失败，尝试旧机器码（含MAC地址，向后兼容）
    machine_id_legacy = get_machine_id_legacy()
    if machine_id_legacy != machine_id:
        val_result_legacy = decode_and_validate(license_str, machine_id_legacy)
        if val_result_legacy["valid"]:
            result["valid"] = True
            result["reason"] = val_result_legacy["reason"]
            result["expiry"] = val_result_legacy["expiry"]
            result["customer"] = val_result_legacy["customer"]
            return result

    # 两种机器码都验证失败，返回新机器码的错误信息
    result["reason"] = val_result["reason"]
    return result


def get_days_remaining(expiry: int) -> int:
    """计算剩余天数"""
    remaining = (expiry - time.time()) / 86400
    return max(0, int(remaining))


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    print("=== 机器指纹 ===")
    mid = get_machine_id()
    print(f"  完整: {mid}")
    print(f"  简写: {get_machine_id_short()}")

    print("\n=== 许可证状态 ===")
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    result = check_license(data_dir)
    print(f"  有效: {result['valid']}")
    print(f"  原因: {result['reason']}")
    if result["valid"]:
        print(f"  客户: {result['customer']}")
        print(f"  到期: {time.strftime('%Y-%m-%d', time.localtime(result['expiry']))}")
        print(f"  剩余: {get_days_remaining(result['expiry'])} 天")
