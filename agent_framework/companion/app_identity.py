"""应用身份：版本信息与 Authenticode 签名，全部走 ctypes，不引第三方依赖。

启动一个程序前要知道"它还是不是用户登记时那一个"。这里给出两样东西：

* ``version_info`` —— 文件版本与描述，用于人类辨认，也进入身份摘要。
* ``signature`` —— Authenticode 的签名状态与发布者；没有签名不等于危险，但一定要
  如实写成"无签名身份"，不能因为读不到证书就报"已签名"。

签名状态与发布者只在**登记/重新核验**时计算：``WinVerifyTrust`` 内部就会对整个 PE
做哈希，每次启动都验一遍的代价和整文件哈希一样，而用户选的是元信息摘要这一档。
启动前复核的是路径/大小/mtime/版本组成的摘要，摘要变了就要求重新登记——那时才会
再算一次签名。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import struct
from typing import Dict, Tuple


SIGNATURE_SIGNED = "signed"
SIGNATURE_UNSIGNED = "unsigned"
SIGNATURE_INVALID = "invalid"
SIGNATURE_UNKNOWN = "unverified"

TRUSTED_SIGNATURE_STATES = (SIGNATURE_SIGNED,)

# WinVerifyTrust 的返回值里，只有 0 是"这次验过了，通过"。其余一律不给"已签名"的结论。
_ERROR_SUCCESS = 0

# wintrust.h 里这个宏的注释与值不一致，实际生效的是 DEFINE_GUID 里的字节序：
# {AEC56FA0-E2B3-11CF-9F51-0060C0203E70}。填错只会回 0x800B0001「找不到提供方」。
WINTRUST_ACTION_GENERIC_VERIFY_V2 = (
    0xAEC56FA0,
    0xE2B3,
    0x11CF,
    (ctypes.c_ubyte * 8)(0x9F, 0x51, 0x00, 0x60, 0xC0, 0x20, 0x3E, 0x70),
)

WTD_UI_NONE = 2
WTD_CHOICE_FILE = 1
WTD_REVOKE_NONE = 0x00000000
WTD_STATEACTION_CLOSE = 2
WTD_REVOCATION_CHECK_NONE = 0x00000010

CERT_QUERY_OBJECT_FILE = 0x00000001
CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBEDDED = 1 << (9 + 1)
CERT_QUERY_FORMAT_FLAG_BINARY = 1 << (0 + 1)
CERT_NAME_SIMPLE_DISPLAY_TYPE = 4
# 同一个姓名，取主体还是取签发者，只差这个标志位。
CERT_NAME_ISSUER_FLAG = 1

# WinVerifyTrust 在本机返回 0x800B0001（TRUST_E_PROVIDER_UNKNOWN）时，连一个刻意写错的
# action GUID 都返回同一个码——说明是这台机器的信任提供方不可用，不是文件的问题。
# 那种情况下只能说"核验不了"，不能说"签名无效"。
TRUST_E_PROVIDER_UNKNOWN = 0x800B0001

PE32_OPTIONAL_HEADER_MAGIC = 0x10B
PE32PLUS_OPTIONAL_HEADER_MAGIC = 0x20B
# IMAGE_DIRECTORY_ENTRY_SECURITY 是数据目录里的第 4 项——不是 3（3 是异常表）。
# 数据目录本身从 PE32 的 96 / PE32+ 的 112 开始，每项 8 字节 (RVA|偏移, 大小)。
PE32_DATA_DIRECTORY_OFFSET = 96
PE32PLUS_DATA_DIRECTORY_OFFSET = 112
CERTIFICATE_DIRECTORY_ENTRY = 4


class AppIdentityError(RuntimeError):
    def __init__(self, message, code="app_identity_error"):
        super().__init__(message)
        self.code = code


_CRYPT32 = None


class _Guid(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _WintrustFileInfo(ctypes.Structure):
    _fields_ = [
        ("cbStruct", wintypes.DWORD),
        ("pcwszFilePath", wintypes.LPCWSTR),
        ("hFile", wintypes.HANDLE),
        ("pgKnownSubject", ctypes.c_void_p),
    ]


class _WintrustData(ctypes.Structure):
    _fields_ = [
        ("cbStruct", wintypes.DWORD),
        ("pPolicyCallbackData", wintypes.LPVOID),
        ("pSIPClientData", wintypes.LPVOID),
        ("dwUIChoice", wintypes.DWORD),
        ("fdwRevocationChecks", wintypes.DWORD),
        ("dwUnionChoice", wintypes.DWORD),
        ("pFile", ctypes.c_void_p),
        ("dwStateAction", wintypes.DWORD),
        ("hWVTStateData", wintypes.HANDLE),
        ("pwszURLReference", wintypes.LPCWSTR),
        ("dwProvFlags", wintypes.DWORD),
        ("dwUIContext", wintypes.DWORD),
        ("pSignatureSettings", wintypes.LPVOID),
    ]


def is_available() -> bool:
    return os.name == "nt"


def _fixed_file_version(value: int) -> str:
    major = (value >> 48) & 0xFFFF
    minor = (value >> 32) & 0xFFFF
    build = (value >> 16) & 0xFFFF
    patch = value & 0xFFFF
    return "{}.{}.{}.{}".format(major, minor, build, patch)


def version_info(path) -> Dict[str, str]:
    """文件版本与描述；读不到就返回空串，不编造。"""
    result = {"file_version": "", "file_description": "", "product_name": ""}
    if not is_available():
        return result
    text = str(path)
    try:
        version = ctypes.windll.version
        size = version.GetFileVersionInfoSizeW(text, ctypes.byref(ctypes.c_uint32()))
        if not size:
            return result
        buffer = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(text, 0, size, buffer):
            return result
        block = ctypes.c_void_p()
        length = ctypes.c_uint32()
        if version.VerQueryValueW(
            buffer,
            "\\",
            ctypes.byref(block),
            ctypes.byref(length),
        ) and length.value >= 20:
            raw = ctypes.string_at(block.value, min(int(length.value), 52))
            ms, ls = struct.unpack_from("<II", raw, 8)
            if ms or ls:
                result["file_version"] = _fixed_file_version((ms << 32) | ls)
        translation = _query_translation(buffer)
        for key, name in (
            ("file_description", "FileDescription"),
            ("product_name", "ProductName"),
        ):
            value = _query_string(buffer, translation, name)
            if value:
                result[key] = value
    except (OSError, ValueError, AttributeError):
        return result
    return result


def _query_translation(buffer) -> Tuple[int, int]:
    version = ctypes.windll.version
    block = ctypes.c_void_p()
    length = ctypes.c_uint32()
    if not version.VerQueryValueW(
        buffer, "\\VarFileInfo\\Translation", ctypes.byref(block), ctypes.byref(length)
    ) or length.value < 4:
        return 0, 1200
    raw = ctypes.string_at(block.value, 4)
    lang, codepage = struct.unpack("<HH", raw)
    return lang, codepage


def _query_string(buffer, translation: Tuple[int, int], name: str) -> str:
    lang, codepage = translation
    version = ctypes.windll.version
    block = ctypes.c_void_p()
    length = ctypes.c_uint32()
    sub = "\\StringFileInfo\\{:04x}{:04x}\\{}".format(lang, codepage, name)
    if not version.VerQueryValueW(
        buffer, sub, ctypes.byref(block), ctypes.byref(length)
    ):
        return ""
    raw = ctypes.string_at(block.value, max(0, int(length.value) - 1) * 2)
    return raw.decode("utf-16-le", errors="replace").strip("\x00").strip()


def certificate_directory(path) -> Tuple[int, int]:
    """PE 证书表的 (文件偏移, 长度)；不是 PE 或没有证书时返回 (0, 0)。

    证书目录是唯一一个"偏移已经是文件位置、不需要再走节表换算"的数据目录，
    所以只读头部就能拿到内嵌的 PKCS#7。
    """
    with open(path, "rb") as handle:
        dos = handle.read(64)
        if len(dos) < 64 or dos[:2] != b"MZ":
            return 0, 0
        new_header = struct.unpack_from("<I", dos, 0x3C)[0]
        handle.seek(new_header)
        pe = handle.read(4 + 20)
        if len(pe) < 24 or pe[:4] != b"PE\x00\x00":
            return 0, 0
        # COFF 头里 SizeOfOptionalHeader 在第 20 字节（签名字段自身占 4 字节）。
        optional_size = struct.unpack_from("<H", pe, 20)[0]
        optional = handle.read(optional_size)
        if len(optional) < 4:
            return 0, 0
        magic = struct.unpack_from("<H", optional, 0)[0]
        if magic == PE32_OPTIONAL_HEADER_MAGIC:
            base = PE32_DATA_DIRECTORY_OFFSET
        elif magic == PE32PLUS_OPTIONAL_HEADER_MAGIC:
            base = PE32PLUS_DATA_DIRECTORY_OFFSET
        else:
            return 0, 0
        entry = base + CERTIFICATE_DIRECTORY_ENTRY * 8
        if len(optional) < entry + 8:
            return 0, 0
        offset, size = struct.unpack_from("<II", optional, entry)
        if offset and size and offset + size > os.path.getsize(path):
            # 证书表写到了文件之外：当成没有内嵌签名，不去读越界的字节。
            return 0, 0
        return int(offset), int(size)


def signature(path) -> Dict[str, object]:
    """Authenticode 状态：``state`` + ``publisher``，两者都只在证据支持时才写。"""
    outcome: Dict[str, object] = {
        "state": SIGNATURE_UNKNOWN,
        "publisher": "",
        "code": "",
        "detail": "",
    }
    if not is_available():
        outcome["code"] = "identity_not_windows"
        outcome["detail"] = "非 Windows 系统无法核验数字签名。"
        return outcome
    text = str(path)
    if not os.path.isfile(text):
        outcome["state"] = SIGNATURE_UNSIGNED
        outcome["code"] = "identity_missing_file"
        outcome["detail"] = "文件不存在，无法核验签名。"
        return outcome
    offset, size = (0, 0)
    try:
        offset, size = certificate_directory(text)
    except OSError as error:
        outcome["code"] = "identity_pe_error"
        outcome["detail"] = str(error)
        return outcome
    if not offset or not size:
        outcome["state"] = SIGNATURE_UNSIGNED
        outcome["code"] = "identity_unsigned"
        outcome["detail"] = "文件没有内嵌 Authenticode 签名。"
        return outcome
    # 发布者直接从内嵌的 PKCS#7 里读，不经过信任提供方；即便本机核验不可用，
    # 这个名字还是拿得到，而它正是身份摘要里最要紧的一项。
    publisher, publisher_detail = _signer_display_name(text)
    verify_state, verify_detail = _winverifytrust(text)
    outcome["state"] = verify_state
    outcome["publisher"] = publisher
    if verify_state != SIGNATURE_SIGNED:
        outcome["code"] = "identity_verify_failed"
        outcome["detail"] = verify_detail
        return outcome
    if not publisher:
        outcome["code"] = "identity_publisher_unknown"
        outcome["detail"] = publisher_detail or "签名有效，但读不到发布者名称。"
    return outcome


def publisher_of(path) -> str:
    """只取内嵌签名里的发布者名，不做 WinVerifyTrust。

    启动前的复核要盯"发布者换没换"，而 WinVerifyTrust 会把整个 PE 哈希一遍，那一步只
    属于登记。CryptQueryObject 只读 PE 证书表里那块 PKCS#7，代价和读版本信息同量级。
    没有内嵌签名时返回空串，跟 ``identity_snapshot`` 给出的发布者同一口径。
    """
    text = str(path or "").strip()
    if not text or not is_available() or not os.path.isfile(text):
        return ""
    try:
        offset, size = certificate_directory(text)
    except OSError:
        return ""
    if not offset or not size:
        return ""
    return _signer_display_name(text)[0]


def _winverifytrust(path: str) -> Tuple[str, str]:
    guid = _Guid(*WINTRUST_ACTION_GENERIC_VERIFY_V2)
    file_info = _WintrustFileInfo()
    file_info.cbStruct = ctypes.sizeof(file_info)
    file_info.pcwszFilePath = path
    data = _WintrustData()
    data.cbStruct = ctypes.sizeof(data)
    data.dwUIChoice = WTD_UI_NONE
    data.fdwRevocationChecks = WTD_REVOKE_NONE
    data.dwUnionChoice = WTD_CHOICE_FILE
    data.pFile = ctypes.cast(ctypes.byref(file_info), ctypes.c_void_p)
    # 扫描时不做吊销检查：那会联网，把一次目录扫描变成几十次网络请求。
    data.dwProvFlags = WTD_REVOCATION_CHECK_NONE
    # VERIFY 时留空 state action，系统才会建立状态句柄；随后用同一个结构 CLOSE 释放。
    code = ctypes.windll.wintrust.WinVerifyTrust(
        wintypes.HWND(-1), ctypes.byref(guid), ctypes.byref(data)
    )
    if data.hWVTStateData:
        data.dwStateAction = WTD_STATEACTION_CLOSE
        ctypes.windll.wintrust.WinVerifyTrust(None, ctypes.byref(guid), ctypes.byref(data))
    if code == _ERROR_SUCCESS:
        return SIGNATURE_SIGNED, ""
    if (code & 0xFFFFFFFF) == TRUST_E_PROVIDER_UNKNOWN:
        # 本机连一个刻意写错的 action GUID 都回同样的码，说明是信任提供方不可用，
        # 而不是这个文件签名坏了。报"无效"会把合法软件误判成风险。
        return (
            SIGNATURE_UNKNOWN,
            "本机 Authenticode 信任提供方不可用（0x{:08X}），无法核验签名。".format(
                code & 0xFFFFFFFF
            ),
        )
    return SIGNATURE_INVALID, "签名核验未通过（系统返回 0x{:08X}）。".format(
        code & 0xFFFFFFFF
    )


def _configure_crypt32():
    """给 crypt32 的函数补上签名。

    ctypes 默认把返回值当 32 位 int，证书上下文的指针会被截断高位，枚举到第二张时
    就是访问违例（本机实测过）。出参也一样：不声明就退化成按值传递，读不到存储句柄。
    """
    global _CRYPT32
    if _CRYPT32 is not None:
        return _CRYPT32
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptQueryObject.restype = wintypes.BOOL
    crypt32.CryptQueryObject.argtypes = [
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    crypt32.CertEnumCertificatesInStore.restype = ctypes.c_void_p
    crypt32.CertEnumCertificatesInStore.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    crypt32.CertCloseStore.restype = wintypes.BOOL
    crypt32.CertCloseStore.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    crypt32.CertGetNameStringW.restype = ctypes.c_int
    crypt32.CertGetNameStringW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_int,
    ]
    _CRYPT32 = crypt32
    return crypt32


def _signer_display_name(path: str) -> Tuple[str, str]:
    """从文件内嵌的 Authenticode 签名里取发布者名称。

    ``CryptQueryObject`` 的 ``pCertContext`` 出参在本机给出的上下文不可用（直接读就是
    访问违例），只用它返回的证书存储自己挑签名者。判据是证书链的方向：链上的每一张
    都会是另一张的签发者，签名者那张不会——比读 basicConstraints 扩展省事，也不需要
    把 ``CERT_CONTEXT`` 的内存布局复刻一遍。枚举推进时上一张已被释放，所以姓名必须在
    还拿着它的那一步就取出来。
    """
    crypt32 = _configure_crypt32()
    encoding = wintypes.DWORD()
    content_type = wintypes.DWORD()
    format_type = wintypes.DWORD()
    store = ctypes.c_void_p()
    cert = ctypes.c_void_p()
    ok = crypt32.CryptQueryObject(
        CERT_QUERY_OBJECT_FILE,
        ctypes.cast(ctypes.c_wchar_p(path), ctypes.c_void_p),
        CERT_QUERY_CONTENT_FLAG_PKCS7_SIGNED_EMBEDDED,
        CERT_QUERY_FORMAT_FLAG_BINARY,
        0,
        ctypes.byref(encoding),
        ctypes.byref(content_type),
        ctypes.byref(format_type),
        ctypes.byref(store),
        ctypes.byref(cert),
        None,
    )
    if not ok or not store:
        return "", "文件里没有可解析的 Authenticode 签名。"
    try:
        subjects = []
        issuers = set()
        current = crypt32.CertEnumCertificatesInStore(store, None)
        while current:
            subject = _display_name(crypt32, current, 0)
            issuer = _display_name(crypt32, current, CERT_NAME_ISSUER_FLAG)
            if subject:
                subjects.append(subject)
            if issuer:
                issuers.add(issuer)
            current = crypt32.CertEnumCertificatesInStore(store, current)
        if not subjects:
            return "", "签名链里读不到任何证书名称。"
        for subject in subjects:
            if subject not in issuers:
                return subject, ""
        return subjects[0], "签名链里只有证书颁发机构，没有取到签名者名称。"
    finally:
        crypt32.CertCloseStore(store, 0)


def _display_name(crypt32, cert, flag: int) -> str:
    size = crypt32.CertGetNameStringW(
        ctypes.c_void_p(cert), CERT_NAME_SIMPLE_DISPLAY_TYPE, flag, None, None, 0
    )
    if size <= 1:
        return ""
    buffer = ctypes.create_unicode_buffer(int(size))
    crypt32.CertGetNameStringW(
        ctypes.c_void_p(cert),
        CERT_NAME_SIMPLE_DISPLAY_TYPE,
        flag,
        None,
        buffer,
        int(size),
    )
    return buffer.value.strip()


def identity_values(path) -> Dict[str, object]:
    """身份摘要的输入：路径、大小、mtime、文件版本。

    mtime 用整数秒，避免同一文件在不同语言区域下格式化出不同字符串。
    """
    target = Path(path)
    try:
        info = target.stat()
    except OSError:
        return {}
    version = version_info(str(target)).get("file_version") or ""
    return {
        "path": os.path.normcase(str(target)),
        "size_bytes": int(info.st_size),
        "mtime": int(info.st_mtime),
        "file_version": version,
    }


# 摘要里放的是"换一个文件就会变"的东西。签名状态不在其中：本机信任提供方不可用时
# 它本来就在 signed / unverified 之间摇摆，放进去会让同一个文件每次算出不同摘要。
DIGEST_FIELDS = ("path", "size_bytes", "mtime", "file_version", "publisher")


def identity_digest(values) -> str:
    """把身份摘要的输入折成一条 sha256；缺字段按空串参与，不跳过。

    跳过缺失字段会让"没有发布者"和"还没算过发布者"折出同一个摘要，复核时分不开。
    """
    joined = "|".join(str((values or {}).get(field, "")) for field in DIGEST_FIELDS)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def identity_snapshot(path) -> Dict[str, object]:
    """登记/重新核验那一刻的完整身份快照：签名结论 + 摘要输入 + 摘要。

    ``signed`` 说的是"文件里有没有内嵌 Authenticode 签名"，``signature_state`` 说的是
    "这一次核验的结论"。本机核验不了的时候前者照样成立、后者只能是 unverified，把两
    件事合成一个布尔会把"查不了"说成"没签名"。

    文件不在原位返回空表：调用方要区分"没算出来"和"算出来是个没签名的文件"。
    """
    text = str(path or "").strip()
    if not text or not os.path.isfile(text):
        return {}
    values = identity_values(text)
    if not values:
        return {}
    outcome = signature(text)
    try:
        values["signed"] = bool(certificate_directory(text)[1])
    except OSError:
        values["signed"] = False
    values["publisher"] = str(outcome.get("publisher") or "")
    values["signature_state"] = str(outcome.get("state") or SIGNATURE_UNKNOWN)
    values["detail"] = str(outcome.get("detail") or "")
    values["digest"] = identity_digest(values)
    return values
