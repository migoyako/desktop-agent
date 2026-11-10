"""S4 — 应用发现：快捷方式解析、App Paths、特殊位置与手动登记。

发现只负责"机器上有什么入口"，授权在另一张表里，所以这一篇用例同时盯着反方向：
扫描不能长出启动权，也不能顺手去算签名。
"""

import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from agent_framework.companion import app_identity
from agent_framework.companion import app_catalog
from agent_framework.companion import app_launch
from agent_framework.companion.access_control import (
    CAPABILITY_APP_LAUNCH,
    MAX_APP_GRANTS,
    MAX_DIRECTORY_GRANTS,
    RESOURCE_APP,
    RESOURCE_DIRECTORY,
    AccessControlError,
    AccessControlService,
    normalize_capabilities,
)
from agent_framework.companion.app_catalog import (
    APP_PATHS_KEYS,
    ENV_BLOCK_SIGNATURE,
    FLAG_ARGUMENTS,
    FLAG_EXP_STRING,
    FLAG_FORCE_NO_LINKINFO,
    FLAG_IDLIST,
    FLAG_LINK_INFO,
    FLAG_NAME,
    FLAG_RELATIVE_PATH,
    FLAG_UNICODE,
    FLAG_WORKING_DIR,
    SPECIAL_LOCATIONS,
    AppCatalogError,
    AppCatalogService,
    discovered_entries,
    expand_environment,
    parse_lnk_bytes,
    parse_lnk_file,
    quote_stripped,
    read_app_paths,
)
from agent_framework.companion.path_safety import app_ref, app_target_key
from agent_framework.companion.store import (
    APP_MISSING,
    APP_READY,
    APP_SOURCE_APP_PATHS,
    APP_SOURCE_MANUAL,
    APP_SOURCE_SPECIAL_FOLDER,
    APP_SOURCE_START_MENU,
    APP_TARGET_EXE,
    APP_TARGET_SHELL,
    SCHEMA_VERSION,
    CompanionStore,
)
from agent_framework.companion.store import (
    APP_CATALOG_MAX_ENTRIES,
    _migrate_v15,
)
from agent_framework.companion.app_launch import (
    APP_LAUNCH_TOOL,
    UNDO_REASON,
    AppLaunchError,
    AppLaunchService,
    openable,
)
from agent_framework.companion.file_actions import NotExecutedError
from agent_framework.companion.models import (
    AgentMode,
    EXECUTOR_APP,
    ProposalStatus,
    RiskLevel,
)
from agent_framework.companion.policy import (
    ASK,
    ALLOW,
    DENY,
    PERMISSION_HINTS,
    PERMISSION_RELAXED,
    PERMISSION_STRICT,
    decide_tool_policy,
    requires_confirmation,
)
from agent_framework.companion.store import APP_BLOCKED, APP_DRIFTED

HEADER = b"\x4c\x00\x00\x00" + bytes(72)


def link_info_offset(data: bytes, items: int = 2) -> int:
    """按 fixture 自己的形状找到 LinkInfo 的起点，省得测试里写死一堆魔数。"""
    at = len(HEADER) + 2
    for _ in range(items):
        at += struct.unpack_from("<H", data, at)[0]
    return at + 2


def build_lnk(
    target=b"C:\\Windows\\notepad.exe",
    name="",
    arguments="",
    working_dir="",
    relative_path="",
    items=2,
    link_info=True,
    idlist=True,
    force_no_link_info=False,
    shell_item=False,
    unicode_names=False,
    link_info_size=None,
    bogus_idlist_size=False,
    local_at=0x10,
    version_field=28,
    count_includes_null=True,
    env_target="",
):
    """按本机量出来的形状拼一个 .lnk，而不是照规范抄——两者位置差 8 字节。

    ``count_includes_null`` 用来同时喂出两种真实写法：规范说 StringData 的计数字段含
    结尾那个 NUL，本机的 Firefox / Python 快捷方式给的却正好是纯字符数。
    """
    flags = 0
    if idlist:
        flags |= FLAG_IDLIST
    if link_info:
        flags |= FLAG_LINK_INFO
    if name:
        flags |= FLAG_NAME
    if relative_path:
        flags |= FLAG_RELATIVE_PATH
    if working_dir:
        flags |= FLAG_WORKING_DIR
    if arguments:
        flags |= FLAG_ARGUMENTS
    if unicode_names:
        flags |= FLAG_UNICODE
    if force_no_link_info:
        flags |= FLAG_FORCE_NO_LINKINFO
    if shell_item:
        flags |= FLAG_EXP_STRING

    header = bytearray(HEADER)
    header[0x14:0x16] = struct.pack("<H", flags)
    out = bytearray(bytes(header))

    body = bytearray()
    for index in range(items):
        # ItemID 的长度字段把自己那 2 字节也算进去。
        payload = bytes([0x1F, 0x50]) + ("item%d" % index).encode("ascii")
        body += struct.pack("<H", 2 + len(payload)) + payload
    body += b"\x00\x00"
    if idlist:
        declared = len(body) if not bogus_idlist_size else 2
        out += struct.pack("<H", declared) + body

    if link_info:
        # +0x00 尺寸 / +0x04 恒为 28 / +0x08 标志 / +0x10 本地基本路径偏移
        fixed = bytearray(0x20)
        # 基本路径永远是码页字节；头部的 Unicode 标志位只管后面的 StringData 段。
        path_bytes = (
            target if isinstance(target, bytes) else str(target).encode("mbcs")
        ) + b"\x00"
        # BasePathOffset 是从 LinkInfo 开头算的，这里紧跟在固定头之后。
        path_at = len(fixed)
        struct.pack_into("<I", fixed, 0x00, path_at + len(path_bytes))
        struct.pack_into("<I", fixed, 0x04, version_field)
        struct.pack_into("<I", fixed, 0x08, 1)
        struct.pack_into("<I", fixed, local_at, path_at)
        if link_info_size is not None:
            struct.pack_into("<I", fixed, 0x00, link_info_size)
        out += bytes(fixed) + path_bytes

    def emit(value):
        if not value:
            return b""
        if unicode_names:
            # 计数字段把结尾那个 NUL 也算进去，ANSI 与 UTF-16 都是"字符数 + 1"。
            encoded = value.encode("utf-16-le") + b"\x00\x00"
            units = len(encoded) // 2
        else:
            encoded = value.encode("mbcs") + b"\x00"
            units = len(encoded)
        declared = units if count_includes_null else units - 1
        return struct.pack("<H", declared) + encoded

    for value, flag in (
        (name, FLAG_NAME),
        (relative_path, FLAG_RELATIVE_PATH),
        (working_dir, FLAG_WORKING_DIR),
        (arguments, FLAG_ARGUMENTS),
    ):
        if flags & flag:
            out += emit(value)
    if env_target:
        # EnvironmentVariablesDataBlock：签名 0xA0000001，紧跟其后的就是那条没展开的
        # ANSI 路径，再后面是同一串的 UTF-16 版本。
        ansi = env_target.encode("mbcs") + b"\x00"
        wide = env_target.encode("utf-16-le") + b"\x00\x00"
        out += b"\x00\x00" + ENV_BLOCK_SIGNATURE + ansi + wide + b"\x00\x00"
    return bytes(out)


class LnkParseTests(unittest.TestCase):
    def test_parses_local_base_path(self):
        parsed = parse_lnk_bytes(build_lnk(target=b"C:\\Windows\\notepad.exe"))
        self.assertEqual(parsed["target"], "C:\\Windows\\notepad.exe")
        self.assertEqual(parsed["reason"], "")

    def test_chinese_path_decodes_with_machine_codepage(self):
        # 基本路径是码页字节，跟头部的 Unicode 标志位无关：中文用户名就是这么存的。
        parsed = parse_lnk_bytes(build_lnk(target="C:\\Users\\黄家禄\\app.exe".encode("mbcs")))
        self.assertEqual(parsed["target"], "C:\\Users\\黄家禄\\app.exe")

    def test_unicode_flag_only_changes_string_data(self):
        parsed = parse_lnk_bytes(
            build_lnk(
                target=b"C:\\Windows\\notepad.exe",
                name="记事本",
                arguments="--flag",
                unicode_names=True,
            )
        )
        self.assertEqual(parsed["target"], "C:\\Windows\\notepad.exe")
        self.assertEqual(parsed["name"], "记事本")
        self.assertEqual(parsed["arguments"], "--flag")

    def test_ansi_strings_when_unicode_flag_clear(self):
        parsed = parse_lnk_bytes(
            build_lnk(target=b"C:\\a.exe", name="Plain", working_dir="C:\\", arguments="-x")
        )
        self.assertEqual(parsed["name"], "Plain")
        self.assertEqual(parsed["working_dir"], "C:\\")
        self.assertEqual(parsed["arguments"], "-x")

    def test_walks_idlist_instead_of_trusting_its_size(self):
        # 长度字段只数项目、不含结束标记；按它偏移会落进项目内部。
        honest = parse_lnk_bytes(build_lnk(target=b"C:\\Windows\\notepad.exe"))
        lying = parse_lnk_bytes(build_lnk(target=b"C:\\Windows\\notepad.exe", bogus_idlist_size=True))
        self.assertEqual(honest["target"], "C:\\Windows\\notepad.exe")
        self.assertEqual(lying["target"], "C:\\Windows\\notepad.exe")

    def test_reasons_for_links_without_a_file_target(self):
        whole = build_lnk(target=b"C:\\Windows\\notepad.exe")
        cases = [
            (build_lnk(idlist=False, link_info=False, name="名字"), "没有记录可执行文件路径"),
            (build_lnk(link_info=False), "没有记录可执行文件路径"),
            (build_lnk(force_no_link_info=True), ""),
            (build_lnk(target=b""), "没有记录可执行文件路径"),
            (whole[: link_info_offset(whole) - 4], "没有结束标记"),
            (b"not a shortcut at all" * 4, "文件头不是快捷方式标识"),
            (b"\x00" * 20, "文件太小"),
        ]
        for data, needle in cases:
            parsed = parse_lnk_bytes(data)
            self.assertEqual(parsed["target"], "", data[:8])
            self.assertTrue(parsed["reason"], data[:8])
            if needle:
                self.assertIn(needle, parsed["reason"])

    def test_link_info_without_idlist_still_resolves(self):
        # 系统自带的 Accessibility / Administrative Tools 就是这种形状：没有目标项列表，
        # 但 LinkInfo 直接跟在头部之后，Shell 照样报得出目标。
        parsed = parse_lnk_bytes(
            build_lnk(target=b"C:\\Windows\\system32\\narrator.exe", idlist=False)
        )
        self.assertEqual(parsed["target"], "C:\\Windows\\system32\\narrator.exe")
        self.assertEqual(parsed["reason"], "")

    def test_environment_block_is_the_only_path_for_some_system_links(self):
        # Accessibility 那一类：头部既没标目标项列表也没标链接信息，路径只藏在签名
        # 0xA0000001 的数据块里，而且是 ``%SystemRoot%`` 这种没展开的写法。
        if os.name != "nt":
            self.skipTest("环境串要靠系统展开")
        parsed = parse_lnk_bytes(
            build_lnk(
                target=b"",
                idlist=False,
                link_info=False,
                env_target="%SystemRoot%\\System32\\notepad.exe",
            )
        )
        self.assertEqual(
            parsed["target"],
            os.path.join(os.environ.get("SystemRoot", ""), "System32", "notepad.exe"),
        )
        self.assertEqual(parsed["reason"], "")

    def test_environment_block_does_not_invent_a_missing_file(self):
        # 数据块里的字节序列谁都能凑出来，所以展开后还得真的有这么个文件才认。
        parsed = parse_lnk_bytes(
            build_lnk(
                target=b"",
                idlist=False,
                link_info=False,
                env_target="%SystemRoot%\\definitely_not_here.exe",
            )
        )
        self.assertEqual(parsed["target"], "")
        self.assertIn("没有记录可执行文件路径", parsed["reason"])

    def test_repeated_environment_blocks_do_not_stall_parsing(self):
        # A signature immediately followed by a terminator used to leave ``at``
        # on the same bytes forever, spinning one thread at full CPU.
        parsed = parse_lnk_bytes(
            build_lnk(
                target=b"",
                idlist=False,
                link_info=False,
                env_target="%SystemRoot%\\definitely_not_here.exe",
            )
        )
        self.assertEqual(parsed["target"], "")
        self.assertTrue(parsed["reason"])

    def test_relative_path_resolves_against_the_link_directory(self):
        # 便携装（本机的微信、B 站就是）基本路径为空，只有相对快捷方式自己所在的目录
        # 才解释得通；卸载之后相对路径还在原位，那条得退回"只记录了相对路径"。
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            program = root / "demo.exe"
            program.write_bytes(b"MZ\x00\x00")
            menu = root / "Programs"
            menu.mkdir()
            link = menu / "Demo.lnk"
            link.write_bytes(
                build_lnk(
                    target=b"",
                    link_info=False,
                    relative_path=os.path.join("..", "demo.exe"),
                )
            )
            parsed = parse_lnk_file(link)
            self.assertEqual(
                os.path.normcase(Path(parsed["target"]).resolve()),
                os.path.normcase(program.resolve()),
            )
            self.assertEqual(parsed["reason"], "")

            program.unlink()
            gone = parse_lnk_file(link)
            self.assertEqual(gone["target"], "")
            self.assertIn("只记录了相对路径", gone["reason"])

    def test_relative_path_falls_back_to_the_working_directory(self):
        # 本机 Nsight 那批相对的是工作目录而不是快捷方式目录；两种口径都能解出文件时，
        # 按规范先认快捷方式自己所在的那个。
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            menu = root / "Programs"
            menu.mkdir()
            (root / "demo.exe").write_bytes(b"MZ\x00\x00")
            work = root / "elsewhere" / "app"
            work.mkdir(parents=True)
            elsewhere = root / "elsewhere" / "demo.exe"
            elsewhere.write_bytes(b"MZ\x00\x00")
            relative = os.path.join("..", "demo.exe")

            link = menu / "Demo.lnk"
            link.write_bytes(
                build_lnk(
                    target=b"",
                    link_info=False,
                    relative_path=relative,
                    working_dir=str(work),
                )
            )
            self.assertEqual(
                os.path.normcase(Path(parse_lnk_file(link)["target"]).resolve()),
                os.path.normcase((root / "demo.exe").resolve()),
            )

            (root / "demo.exe").unlink()
            resolved = parse_lnk_file(link)
            self.assertEqual(
                os.path.normcase(Path(resolved["target"]).resolve()),
                os.path.normcase(elsewhere.resolve()),
            )
            self.assertEqual(resolved["reason"], "")

    def test_string_count_may_exclude_the_terminating_null(self):
        # 本机 Firefox / Python 快捷方式的计数字段是纯字符数，不含 NUL：按规范减一
        # 就会吃掉最后一个字母，参数里收尾的那个引号也是这么丢的。
        parsed = parse_lnk_bytes(
            build_lnk(
                target=b"C:\\Program Files\\Mozilla Firefox\\firefox.exe",
                working_dir="C:\\Program Files\\Mozilla Firefox",
                arguments='--osint --background-task"',
                count_includes_null=False,
            )
        )
        self.assertEqual(parsed["working_dir"], "C:\\Program Files\\Mozilla Firefox")
        self.assertEqual(parsed["arguments"], '--osint --background-task"')

    def test_unicode_strings_survive_the_other_count_convention(self):
        parsed = parse_lnk_bytes(
            build_lnk(
                target=b"C:\\Windows\\notepad.exe",
                name="记事本",
                arguments="--flag",
                unicode_names=True,
                count_includes_null=False,
            )
        )
        self.assertEqual(parsed["name"], "记事本")
        self.assertEqual(parsed["arguments"], "--flag")

    def test_shell_item_flag_is_recorded_not_an_error(self):
        parsed = parse_lnk_bytes(build_lnk(link_info=False, shell_item=True))
        self.assertTrue(parsed["shell_item"])
        self.assertIn("shell 命名空间", parsed["reason"])

    def test_sloppy_link_infos_size_does_not_lose_the_target(self):
        # LinkInfo 尺寸写歪的快捷方式（本机"控制面板"就是这样）：路径已经拿到了，
        # 后缀字段落在字符串段中间、读不出来就算了，不该因此把整条记录作废。
        full = build_lnk(target=b"C:\\Windows\\notepad.exe", name="名字")
        start = link_info_offset(full)
        honest = struct.unpack_from("<I", full, start)[0]
        sloppy = full[:start] + struct.pack("<I", honest - 8) + full[start + 4 :]
        parsed = parse_lnk_bytes(sloppy)
        self.assertEqual(parsed["target"], "C:\\Windows\\notepad.exe")
        self.assertEqual(parsed["name"], "")

    def test_truncated_bytes_never_raise(self):
        whole = build_lnk(target=b"C:\\Windows\\notepad.exe", name="n", arguments="a")
        for cut in range(0, len(whole), 7):
            self.assertIsInstance(parse_lnk_bytes(whole[:cut]), dict)

    def test_parse_lnk_file_reports_read_errors(self):
        with tempfile.TemporaryDirectory() as raw:
            missing = Path(raw) / "gone.lnk"
            self.assertIn("读取快捷方式失败", parse_lnk_file(missing)["reason"])
            good = Path(raw) / "ok.lnk"
            good.write_bytes(build_lnk(target=b"C:\\Windows\\notepad.exe"))
            self.assertEqual(parse_lnk_file(good)["target"], "C:\\Windows\\notepad.exe")
            huge = Path(raw) / "huge.lnk"
            huge.write_bytes(build_lnk() + b"\x00" * (app_catalog.MAX_LNK_BYTES + 1))
            self.assertIn("异常地大", parse_lnk_file(huge)["reason"])


class RegistryAndExpansionTests(unittest.TestCase):
    def test_quote_stripped(self):
        self.assertEqual(
            quote_stripped('"C:\\Program Files\\X\\x.exe" -Embedding'),
            "C:\\Program Files\\X\\x.exe",
        )
        self.assertEqual(quote_stripped("C:\\a\\b.exe -y"), "C:\\a\\b.exe")
        self.assertEqual(quote_stripped("C:\\a\\runme"), "C:\\a\\runme")
        self.assertEqual(quote_stripped(""), "")

    def test_expand_environment_handles_system_variable_names(self):
        if os.name != "nt":
            self.skipTest("只在 Windows 上有环境串展开")
        self.assertEqual(
            expand_environment("%SystemRoot%\\System32\\notepad.exe"),
            os.path.join(os.environ.get("SystemRoot", ""), "System32", "notepad.exe"),
        )

    def test_read_app_paths_shape(self):
        if os.name != "nt":
            self.skipTest("App Paths 是注册表里的东西")
        entries = read_app_paths()
        self.assertTrue(entries, "这台机器上应该至少能读到一条 App Paths")
        for name, target, working_dir in entries:
            self.assertTrue(name and target)
            self.assertIsInstance(working_dir, str)
        self.assertEqual(len(APP_PATHS_KEYS), 3)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exe = self.root / "demo.exe"
        self.exe.write_bytes(b"MZ\x00\x00")
        self.links = self.root / "links"
        self.links.mkdir()

    def write_link(self, name, target):
        path = self.links / name
        path.write_bytes(build_lnk(target=target))
        return path

    def test_entries_carry_status_and_never_a_signature(self):
        rows, skipped = discovered_entries(
            links=[self.write_link("Demo.lnk", str(self.exe).encode("mbcs"))],
            app_paths=[("ghost.exe", str(self.root / "ghost.exe"), "")],
            include_special=False,
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(skipped), 0)
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(by_name["Demo"]["status"], APP_READY)
        self.assertEqual(by_name["Demo"]["source"], APP_SOURCE_START_MENU)
        self.assertEqual(by_name["ghost.exe"]["status"], APP_MISSING)
        self.assertEqual(by_name["ghost.exe"]["source"], APP_SOURCE_APP_PATHS)
        self.assertNotIn("publisher", by_name["Demo"])
        self.assertNotIn("signature_state", by_name["Demo"])
        self.assertGreater(by_name["Demo"]["size_bytes"], 0)

    def test_unparsable_links_are_skipped_with_a_reason(self):
        broken = self.links / "broken.lnk"
        broken.write_bytes(build_lnk(idlist=False, link_info=False, name="坏掉的"))
        rows, skipped = discovered_entries(
            links=[broken], app_paths=[], include_special=False
        )
        self.assertEqual(rows, [])
        self.assertEqual(skipped[0]["name"], "broken")
        self.assertIn("没有记录可执行文件路径", skipped[0]["reason"])

    def test_special_locations_use_their_own_kind_and_source(self):
        rows, _ = discovered_entries(links=[], app_paths=[], include_special=True)
        recycle = [row for row in rows if row["name"] == "回收站"]
        self.assertEqual(len(recycle), 1)
        self.assertEqual(recycle[0]["target_kind"], APP_TARGET_SHELL)
        self.assertEqual(recycle[0]["source"], APP_SOURCE_SPECIAL_FOLDER)
        self.assertEqual(recycle[0]["status"], APP_READY)
        self.assertEqual(len(rows), len(SPECIAL_LOCATIONS))

    def test_app_target_key_matches_the_lookup_rule(self):
        # 启动前复核要用同一个键去查，两边算出来的东西必须一模一样。
        for row in discovered_entries(links=[], app_paths=[], include_special=True)[0]:
            self.assertEqual(row["target_key"], app_target_key(row["target"]))
        self.assertEqual(
            app_target_key("shell:::{645FF040-509C-4060-A8D2-06E02583C470}".upper()),
            app_target_key("shell:::{645ff040-509c-4060-a8d2-06e02583c470}"),
        )


class AppCatalogServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = CompanionStore(self.root / "companion.db")
        self.service = AppCatalogService(self.store)
        self.exe = self.root / "demo.exe"
        self.exe.write_bytes(b"MZ\x00\x00")
        self.links = self.root / "links"
        self.links.mkdir()
        self.link = self.links / "Demo.lnk"
        self.link.write_bytes(build_lnk(target=str(self.exe).encode("mbcs")))

    def test_scan_lands_in_the_catalog_and_grants_stay_empty(self):
        result = self.service.scan(
            links=[self.link],
            app_paths=[("other.exe", str(self.root / "other.exe"), "")],
            include_special=True,
        )
        self.assertEqual(result["written"], result["discovered"])
        self.assertGreaterEqual(result["discovered"], 2 + len(SPECIAL_LOCATIONS))
        found = self.store.find_app_catalog(str(self.exe))
        self.assertIsNotNone(found)
        self.assertEqual(found["name"], "Demo")
        self.assertEqual(found["target_kind"], APP_TARGET_EXE)
        self.assertEqual(found["publisher"], "")
        self.assertEqual(found["signature_state"], "")
        self.assertEqual(self.store.count_app_catalog(status=APP_MISSING), 1)
        # 发现不是授权：扫完一遍，一条启动许可都不该多出来。
        self.assertEqual(self.store.list_access_grants(), [])

    def test_rescan_keeps_the_same_app_id(self):
        first = self.service.scan(links=[self.link], app_paths=[], include_special=False)
        stable = self.store.find_app_catalog(str(self.exe))["app_id"]
        second = self.service.scan(links=[self.link], app_paths=[], include_special=False)
        again = self.store.find_app_catalog(str(self.exe))
        self.assertEqual(again["app_id"], stable)
        self.assertEqual(again["app_id"], app_ref(app_target_key(str(self.exe))))
        self.assertEqual(first["total"], second["total"])

    def test_same_target_from_two_sources_is_one_row(self):
        self.service.scan(
            links=[self.link],
            app_paths=[("demo.exe", str(self.exe), "")],
            include_special=False,
        )
        self.assertEqual(self.store.count_app_catalog(), 1)

    def test_manual_registration_is_the_only_writer_that_marks_its_source(self):
        row = self.service.register_manual("演示", str(self.exe))
        self.assertEqual(row["source"], APP_SOURCE_MANUAL)
        self.assertEqual(row["status"], APP_READY)
        shell = self.service.register_manual("回收站", "shell:RecycleBinFolder")
        self.assertEqual(shell["target_kind"], APP_TARGET_SHELL)
        with self.assertRaises(AppCatalogError) as raised:
            self.service.register_manual("没这个东西", str(self.root / "nope.exe"))
        self.assertEqual(raised.exception.code, "app_target_missing")
        with self.assertRaises(AppCatalogError) as empty:
            self.service.register_manual("空", "  ")
        self.assertEqual(empty.exception.code, "app_target_required")

    def test_scan_keeps_a_verdict_it_could_not_have_reached(self):
        self.service.scan(links=[self.link], app_paths=[], include_special=False)
        row = self.store.find_app_catalog(str(self.exe))
        # drifted 是一次真实身份复核的结论，扫描只看得见"文件在不在"，
        # 没有资格把它改回 ready——那等于把"摘要变了"这个事实洗掉。
        self.store.set_app_catalog_status(row["app_id"], "drifted", "身份摘要变了")
        self.service.scan(links=[self.link], app_paths=[], include_special=False)
        self.assertEqual(self.store.get_app_catalog(row["app_id"])["status"], "drifted")
        # 目标真的不见了时，missing 是更强的事实，照常覆盖。
        self.exe.unlink()
        self.service.scan(links=[self.link], app_paths=[], include_special=False)
        self.assertEqual(self.store.get_app_catalog(row["app_id"])["status"], APP_MISSING)

    def test_rescan_does_not_erase_an_identity_computed_at_registration(self):
        self.service.scan(links=[self.link], app_paths=[], include_special=False)
        row = self.store.find_app_catalog(str(self.exe))
        self.store.upsert_app_catalog(
            [
                dict(
                    row,
                    publisher="示例公司",
                    signed=True,
                    signature_state="signed",
                    identity_digest="digest-1",
                    last_checked_at="2026-11-05T00:00:00+00:00",
                )
            ]
        )
        self.service.scan(links=[self.link], app_paths=[], include_special=False)
        again = self.store.get_app_catalog(row["app_id"])
        self.assertEqual(again["publisher"], "示例公司")
        self.assertTrue(again["signed"])
        self.assertEqual(again["signature_state"], "signed")
        self.assertEqual(again["identity_digest"], "digest-1")

    def test_entry_cap_is_enforced(self):
        rows = []
        for index in range(5):
            handle = self.root / ("app%d.exe" % index)
            handle.write_bytes(b"MZ")
            link = self.links / ("L%d.lnk" % index)
            link.write_bytes(build_lnk(target=str(handle).encode("mbcs")))
            rows.append(link)
        result = AppCatalogService(self.store, max_entries=3).scan(
            links=rows, app_paths=[], include_special=False
        )
        self.assertEqual(result["discovered"], 3)
        self.assertTrue(result["truncated"])
        self.assertEqual(self.store.count_app_catalog(), 3)

    def test_lookup_returns_ready_rows_only(self):
        self.service.scan(
            links=[self.link],
            app_paths=[("ghost.exe", str(self.root / "ghost.exe"), "")],
            include_special=False,
        )
        self.assertEqual([row["name"] for row in self.service.lookup("dem")], ["Demo"])
        self.assertEqual(self.service.lookup("ghost"), [])
        self.assertEqual(self.service.stats()["ready"], 1)

    def test_schema_version_bumped_for_the_catalog(self):
        self.assertGreaterEqual(SCHEMA_VERSION, 15)
        with self.store._connection() as connection:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertIn("app_catalog", names)


class MigrationTests(unittest.TestCase):
    def test_v14_database_gains_the_catalog(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "companion.db"
            store = CompanionStore(path)
            with store._connection() as connection:
                version = int(
                    connection.execute("PRAGMA user_version").fetchone()[0]
                )
            self.assertEqual(version, SCHEMA_VERSION)
            # 老库（没有 app_catalog）直接跑一次 v15 迁移也能补上表。
            with store._connection() as connection:
                connection.execute("DROP TABLE app_catalog")
                _migrate_v15(connection)
                created = connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'app_catalog'"
                ).fetchone()
            self.assertIsNotNone(created)
            self.assertEqual(store.count_app_catalog(), 0)


class IdentityTests(unittest.TestCase):
    """身份摘要：登记那一刻算，启动前只比摘要，绝不每次启动哈希整个文件。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = CompanionStore(self.root / "companion.db")
        self.service = AppCatalogService(self.store)
        self.exe = self.root / "demo.exe"
        self.exe.write_bytes(b"MZ\x00\x00" + bytes(80))

    def row(self):
        return self.store.find_app_catalog(str(self.exe))

    def test_snapshot_folds_the_metadata_into_one_digest(self):
        values = {
            "path": os.path.normcase(str(self.exe)),
            "size_bytes": 86,
            "mtime": 100,
            "file_version": "",
            "publisher": "",
        }
        self.assertEqual(len(app_identity.identity_digest(values)), 64)
        other = dict(values, size_bytes=87)
        self.assertNotEqual(
            app_identity.identity_digest(values), app_identity.identity_digest(other)
        )
        # 缺字段按空串参与，所以"没有发布者"和" publisher 还没算"折不出同一个摘要之外的花样。
        self.assertEqual(
            app_identity.identity_digest(values),
            app_identity.identity_digest(dict(values, file_version="")),
        )

    def test_snapshot_of_a_missing_file_is_empty(self):
        self.assertEqual(app_identity.identity_snapshot(self.root / "nope.exe"), {})
        self.assertEqual(app_identity.publisher_of(self.root / "nope.exe"), "")

    def test_capture_writes_the_identity_columns(self):
        self.service.register_manual("演示", str(self.exe))
        outcome = self.service.capture_identity(self.row()["app_id"])
        self.assertTrue(outcome["captured"])
        stored = self.row()
        self.assertEqual(
            stored["signature_state"], "unsigned" if os.name == "nt" else "unverified"
        )
        self.assertFalse(stored["signed"])
        self.assertEqual(len(stored["identity_digest"]), 64)
        self.assertEqual(stored["status"], APP_READY)
        self.assertGreater(stored["size_bytes"], 0)
        self.assertTrue(stored["last_checked_at"])
        # 摘要那一列真的对应摘要本身：改名时把 identity_digest 落成别的值，这里就会断。
        values = app_identity.identity_values(self.exe)
        values["publisher"] = ""
        self.assertEqual(
            stored["identity_digest"], app_identity.identity_digest(values)
        )

    def test_recheck_notices_a_replaced_file(self):
        self.service.register_manual("演示", str(self.exe))
        app_id = self.row()["app_id"]
        self.service.capture_identity(app_id)
        baseline = self.row()["identity_digest"]
        self.assertTrue(self.service.recheck_identity(app_id)["ok"])

        # 换文件：大小也变，摘要跟着变——这正是这一步要看住的东西。
        self.exe.write_bytes(b"XX\x00\x00" + bytes(90))
        outcome = self.service.recheck_identity(app_id)
        self.assertFalse(outcome["ok"])
        self.assertTrue(outcome["changed"])
        self.assertEqual(self.row()["status"], "drifted")
        self.assertIn("重新登记", self.row()["last_error"])

        # 复核只看摘要，不改基线：drifted 是一次结论，重新登记才允许清零。
        self.service.capture_identity(app_id)
        again = self.row()
        self.assertEqual(again["status"], APP_READY)
        self.assertEqual(again["last_error"], "")
        self.assertNotEqual(again["identity_digest"], baseline)
        self.assertTrue(self.service.recheck_identity(app_id)["ok"])

    def test_recheck_without_a_baseline_refuses(self):
        # 只扫过一遍、还没登记：没有基线可比，把眼前这个文件当基线正是这一步要防的事。
        self.service.register_manual("演示", str(self.exe))
        outcome = self.service.recheck_identity(self.row()["app_id"])
        self.assertFalse(outcome["ok"])
        self.assertIn("身份基线", outcome["reason"])

    def test_recheck_reports_a_vanished_target(self):
        self.service.register_manual("演示", str(self.exe))
        app_id = self.row()["app_id"]
        self.service.capture_identity(app_id)
        self.exe.unlink()
        outcome = self.service.recheck_identity(app_id)
        self.assertFalse(outcome["ok"])
        self.assertEqual(self.row()["status"], APP_MISSING)

    def test_shell_targets_have_no_file_identity(self):
        row = self.service.register_manual("回收站", "shell:RecycleBinFolder")
        captured = self.service.capture_identity(row["app_id"])
        self.assertFalse(captured["captured"])
        self.assertEqual(captured["row"]["identity_digest"], "")
        self.assertTrue(self.service.recheck_identity(row["app_id"])["ok"])

    def test_capture_refuses_an_unknown_app(self):
        with self.assertRaises(AppCatalogError) as raised:
            self.service.capture_identity("app-does-not-exist")
        self.assertEqual(raised.exception.code, "app_not_in_catalog")
        with self.assertRaises(AppCatalogError) as other:
            self.service.recheck_identity("app-does-not-exist")
        self.assertEqual(other.exception.code, "app_not_in_catalog")


class AppGrantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = CompanionStore(self.root / "companion.db")
        self.service = AppCatalogService(self.store)
        self.access = AccessControlService(self.store)
        self.exe = self.root / "demo.exe"
        self.exe.write_bytes(b"MZ\x00\x00" + bytes(80))

    def allow(self, target=None):
        """登记一条应用授权，并把身份基线一起算出来。"""
        text = str(target or self.exe)
        self.service.register_manual(Path(text).stem or "演示", text)
        row = self.store.find_app_catalog(text)
        self.access.grant_app(row["target"])
        self.service.capture_identity(row["app_id"])
        return row["app_id"]

    def unregister(self):
        for grant in self.store.list_access_grants(resource_kind="app"):
            if grant["status"] == "active":
                self.access.revoke_grant(grant["grant_id"])

    def test_capability_sets_are_per_resource_kind(self):
        self.assertEqual(
            normalize_capabilities(["app-launch"], RESOURCE_APP), ("app-launch",)
        )
        for values, kind in ((["fs-read"], RESOURCE_APP), (["app-launch"], RESOURCE_DIRECTORY)):
            with self.assertRaises(AccessControlError) as raised:
                normalize_capabilities(values, kind)
            self.assertEqual(raised.exception.code, "unknown_capability")
        # 老调用点不传类型也照样按目录校验，S1～S3 的行为不受影响。
        self.assertEqual(normalize_capabilities(["fs-read"]), ("fs-read",))
        with self.assertRaises(AccessControlError) as wrong:
            normalize_capabilities(["fs-read"], "printer")
        self.assertEqual(wrong.exception.code, "unknown_resource_kind")

    def test_grant_app_is_one_row_per_target_and_idempotent(self):
        app_id = self.allow()
        target = self.store.get_app_catalog(app_id)["target"]
        first = self.access.find_app_grant(target)
        self.assertEqual(first.resource_kind, RESOURCE_APP)
        self.assertEqual(first.capabilities, (CAPABILITY_APP_LAUNCH,))
        self.assertEqual(first.status, "active")
        self.access.grant_app(target)
        self.assertEqual(self.store.count_access_grants("active", RESOURCE_APP), 1)
        self.assertEqual(self.access.find_app_grant(target).grant_id, first.grant_id)

    def test_lookup_is_case_insensitive_like_the_catalog(self):
        app_id = self.allow()
        found = self.access.find_app_grant(str(self.exe).upper())
        self.assertIsNotNone(found)
        self.assertEqual(
            found.grant_id, self.access.find_app_grant(self.store.get_app_catalog(app_id)["target"]).grant_id
        )

    def test_launch_grant_answers_with_a_reason(self):
        app_id = self.allow()
        target = self.store.get_app_catalog(app_id)["target"]
        grant, reason = self.access.app_launch_grant(target)
        self.assertIsNotNone(grant)
        self.assertEqual(reason, "")
        self.unregister()
        missing, why = self.access.app_launch_grant(target)
        self.assertIsNone(missing)
        self.assertIn("撤销", why)
        absent, other = self.access.app_launch_grant(str(self.root / "never.lnk"))
        self.assertIsNone(absent)
        self.assertIn("还没有登记", other)

    def test_directory_and_app_caps_are_tracked_separately(self):
        for index in range(MAX_DIRECTORY_GRANTS):
            folder = self.root / ("dir%d" % index)
            folder.mkdir()
            self.access.grant_directory(folder, ["fs-read"])
        self.allow()
        self.assertEqual(self.store.count_access_grants("active", RESOURCE_DIRECTORY), 16)
        self.assertEqual(self.store.count_access_grants("active", RESOURCE_APP), 1)
        self.assertEqual(len(self.access.list_grants()), MAX_DIRECTORY_GRANTS)
        self.assertEqual(len(self.access.list_grants(resource_kind=RESOURCE_APP)), 1)

    def test_app_grant_cap(self):
        for index in range(MAX_APP_GRANTS):
            handle = self.root / ("app%d.exe" % index)
            handle.write_bytes(b"MZ\x00\x00" + bytes(8))
            self.allow(handle)
        extra = self.root / "one_more.exe"
        extra.write_bytes(b"MZ\x00\x00" + bytes(8))
        with self.assertRaises(AccessControlError) as raised:
            self.allow(extra)
        self.assertEqual(raised.exception.code, "grant_limit")

    def test_grant_needs_a_target(self):
        with self.assertRaises(AccessControlError) as raised:
            self.access.grant_app("  ")
        self.assertEqual(raised.exception.code, "app_target_required")

    def test_leases_stay_a_directory_thing(self):
        app_id = self.allow()
        target = self.store.get_app_catalog(app_id)["target"]
        grant = self.access.find_app_grant(target)
        with self.assertRaises(AccessControlError) as raised:
            self.access.issue_lease(grant.grant_id, "task-1", CAPABILITY_APP_LAUNCH)
        self.assertEqual(raised.exception.code, "lease_resource_kind")

    def test_rescanning_never_grants_anything(self):
        self.allow()
        self.unregister()
        self.service.scan(links=[], app_paths=[("demo.exe", str(self.exe), "")])
        self.assertEqual(
            self.store.count_access_grants("active", RESOURCE_APP), 0
        )


class LaunchServiceTests(unittest.TestCase):
    """启动执行器：登记即授权、动手之前复核、回执只写真的发生过的账。

    真起进程的用例一律打在 ``launch_row`` 上——这一篇要验的是"什么时候才允许动手"
    和"动手以后账怎么写"，不是 Windows 能不能拉起一个 notepad。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = CompanionStore(self.root / "companion.db")
        self.catalog = AppCatalogService(self.store)
        self.access = AccessControlService(self.store)
        self.service = AppLaunchService(self.store, self.catalog, self.access)
        self.exe = self.root / "demo.exe"
        self.exe.write_bytes(b"MZ\x00\x00" + bytes(80))

    def allow(self, target=None, name="演示"):
        text = str(target or self.exe)
        self.catalog.register_manual(name, text)
        row = self.store.find_app_catalog(text)
        self.access.grant_app(row["target"])
        self.catalog.capture_identity(row["app_id"])
        return row["app_id"]

    def proposal(self, term="演示"):
        return self.service.create_proposal(
            session_id="s-1",
            term=term,
            mode=AgentMode.WORK,
            source_message_id=7,
        )

    def launched(self, pid=4242):
        """把真正的 CreateProcessW 换成一个可断言的假动作。"""
        return mock.patch(
            "agent_framework.companion.app_launch.launch_row",
            return_value={
                "launcher": "create_process",
                "pid": pid,
                "command_line": '"C:\\\\demo.exe"',
            },
        )

    def test_policy_matrix_directly_launches_registered_apps(self):
        # 陪伴/安静完全不答应；工作与执行按「登记即授权」直接执行；保守档不放宽。
        for mode in (AgentMode.CHAT, AgentMode.QUIET):
            self.assertEqual(decide_tool_policy(mode, RiskLevel.APP_LAUNCH), DENY)
        for mode in (AgentMode.WORK, AgentMode.EXECUTE):
            self.assertEqual(decide_tool_policy(mode, RiskLevel.APP_LAUNCH), ALLOW)
            self.assertEqual(
                decide_tool_policy(mode, RiskLevel.APP_LAUNCH, PERMISSION_RELAXED),
                ALLOW,
            )
            self.assertEqual(
                decide_tool_policy(mode, RiskLevel.APP_LAUNCH, PERMISSION_STRICT),
                DENY,
            )
            self.assertFalse(
                requires_confirmation(mode, RiskLevel.APP_LAUNCH, PERMISSION_STRICT)
            )

    def test_launch_is_a_proposal_not_a_direct_answer(self):
        # ALLOW 只是"不弹确认卡"，提案、账本与回执一步都不能少：策略说明里承诺的就是这个。
        app_id = self.allow()
        proposal = self.proposal()
        self.assertEqual(proposal.executor, EXECUTOR_APP)
        self.assertEqual(proposal.tool_name, APP_LAUNCH_TOOL)
        self.assertIs(proposal.risk, RiskLevel.APP_LAUNCH)
        self.assertFalse(proposal.reversible)
        self.assertEqual(
            proposal.normalized_arguments["app_id"], app_id
        )
        self.assertIn("启动", PERMISSION_HINTS["balanced"])

    def test_command_line_comes_from_the_row_not_from_the_model(self):
        self.allow()
        with self.assertRaises(TypeError):
            self.service.create_proposal(
                session_id="s-1",
                term="演示",
                mode=AgentMode.WORK,
                arguments="/SILENT",
            )
        proposal = self.proposal()
        self.assertEqual(
            sorted(proposal.normalized_arguments),
            ["app_id", "source_message_id"],
        )
        state = proposal.expected_state
        self.assertEqual(state["target"], str(self.exe))
        self.assertTrue(state["identity_digest"])
        self.assertIn("demo.exe", proposal.user_visible_plan)

    def test_arguments_recorded_at_registration_reach_the_command_line(self):
        # 快捷方式里存的参数是登记时冻结的一部分，不是模型临时能塞的东西。
        self.catalog.register_manual("带参数", str(self.exe), arguments="/silent")
        row = self.store.find_app_catalog(str(self.exe))
        self.access.grant_app(row["target"])
        self.catalog.capture_identity(row["app_id"])
        with mock.patch(
            "agent_framework.companion.app_launch.create_process", return_value=999
        ) as create:
            receipt = self.service.execute(self.proposal("带参数"))
        command_line = create.call_args[0][0]
        self.assertIn("/silent", command_line)
        self.assertTrue(command_line.startswith('"'))
        self.assertEqual(create.call_args[0][1], "")
        self.assertIn("已启动", receipt.summary)

    def test_unregistered_app_never_becomes_a_proposal(self):
        self.catalog.register_manual("演示", str(self.exe))
        self.catalog.capture_identity(self.store.find_app_catalog(str(self.exe))["app_id"])
        with self.assertRaises(AppLaunchError) as raised:
            self.proposal()
        self.assertEqual(raised.exception.code, "app_not_allowed")
        self.assertIn("还没有登记", str(raised.exception))

    def test_revoked_grant_names_revoking_as_the_next_step(self):
        app_id = self.allow()
        grant = self.access.find_app_grant(str(self.exe))
        self.access.revoke_grant(grant.grant_id)
        with self.assertRaises(AppLaunchError) as raised:
            self.proposal()
        self.assertEqual(raised.exception.code, "app_not_allowed")
        self.assertIn("已撤销", str(raised.exception))

    def test_blocked_row_is_refused_with_its_own_reason(self):
        app_id = self.allow()
        self.store.set_app_catalog_status(app_id, APP_BLOCKED, "这条被维护名单挡住了。")
        decision = self.service.decide(self.store.get_app_catalog(app_id))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "app_blocked")
        self.assertIn("维护名单", decision.reason)

    def test_replaced_file_is_refused_and_asks_for_re_registration(self):
        app_id = self.allow()
        self.exe.write_bytes(b"MZ\x00\x00" + bytes(90))
        decision = self.service.decide(self.store.get_app_catalog(app_id))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "app_drifted")
        self.assertIn("重新登记", decision.reason)
        self.assertEqual(
            self.store.get_app_catalog(app_id)["status"], APP_DRIFTED
        )

    def test_vanished_target_is_missing_not_drifted(self):
        app_id = self.allow()
        self.exe.unlink()
        decision = self.service.decide(self.store.get_app_catalog(app_id))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "app_missing")
        self.assertNotIn("重新登记", decision.reason)

    def test_row_without_a_baseline_refuses_to_launch(self):
        app_id = self.allow()
        with self.store._connection() as connection:
            connection.execute(
                "UPDATE app_catalog SET identity_digest = '' WHERE app_id = ?",
                (app_id,),
            )
        decision = self.service.decide(self.store.get_app_catalog(app_id))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "app_unchecked")
        self.assertIn("身份基线", decision.reason)

    def test_name_lookup_only_counts_when_it_is_unique(self):
        other = self.root / "other.exe"
        other.write_bytes(b"MZ\x00\x00" + bytes(8))
        self.allow(self.exe, name="同名")
        self.allow(other, name="同名")
        with self.assertRaises(AppLaunchError) as raised:
            self.service.create_proposal(
                session_id="s-1", term="同名", mode=AgentMode.WORK
            )
        self.assertEqual(raised.exception.code, "app_not_found")
        self.assertIsNone(self.service.resolve("不存在的名字"))

    def test_execute_leaves_a_receipt_that_cannot_be_undone(self):
        self.allow()
        proposal = self.proposal()
        with self.launched(pid=4321) as fake:
            receipt = self.service.execute(proposal)
        self.assertEqual(receipt.status, "completed")
        self.assertEqual(receipt.result["pid"], 4321)
        self.assertEqual(receipt.result["launcher"], "create_process")
        self.assertEqual(receipt.result["app_id"], proposal.expected_state["app_id"])
        self.assertFalse(receipt.undoable)
        self.assertEqual(receipt.undo_status, "unavailable")
        self.assertIn("4321", receipt.summary)
        fake.assert_called_once()
        with self.assertRaises(AppLaunchError) as undo:
            self.service.undo(receipt)
        self.assertEqual(undo.exception.code, "app_undo_unavailable")
        self.assertEqual(str(undo.exception), UNDO_REASON)

    def test_shell_open_evidence_does_not_invent_a_pid(self):
        self.allow()
        with mock.patch(
            "agent_framework.companion.app_launch.launch_row",
            return_value={
                "launcher": "shell_execute",
                "pid": 0,
                "shell_instance": 123456,
                "command_line": "shell:RecycleBinFolder",
            },
        ):
            receipt = self.service.execute(self.proposal())
        self.assertEqual(receipt.result["pid"], 0)
        self.assertNotIn("进程号", receipt.summary)
        self.assertIn("已通过 shell 打开", receipt.summary)

    def test_revoked_while_waiting_stops_before_any_process(self):
        self.allow()
        proposal = self.proposal()
        grant = self.access.find_app_grant(str(self.exe))
        self.access.revoke_grant(grant.grant_id)
        with self.launched() as fake, self.assertRaises(NotExecutedError) as raised:
            self.service.execute(proposal)
        fake.assert_not_called()
        self.assertIn("已撤销", str(raised.exception))

    def test_replaced_while_waiting_stops_before_any_process(self):
        self.allow()
        proposal = self.proposal()
        self.exe.write_bytes(b"MZ\x00\x00" + bytes(90))
        with self.launched() as fake, self.assertRaises(NotExecutedError):
            self.service.execute(proposal)
        fake.assert_not_called()

    def test_re_registered_while_waiting_is_refused_on_the_digest(self):
        # 提案冻的是当时那份基线；期间重新登记过，就说明"要启动的那个文件"已经换了含义。
        self.allow()
        proposal = self.proposal()
        self.exe.write_bytes(b"MZ\x00\x00" + bytes(90))
        self.catalog.capture_identity(proposal.expected_state["app_id"])
        with self.launched() as fake, self.assertRaises(NotExecutedError) as raised:
            self.service.execute(proposal)
        fake.assert_not_called()
        self.assertIn("重新发起", str(raised.exception))

    def test_expired_proposal_is_not_executed(self):
        self.allow()
        proposal = self.proposal()
        proposal.expected_state["expires_at"] = "2020-01-01T00:00:00+00:00"
        with self.launched() as fake, self.assertRaises(NotExecutedError) as raised:
            self.service.execute(proposal)
        fake.assert_not_called()
        self.assertIn("过期", str(raised.exception))

    def test_tampered_arguments_are_refused(self):
        self.allow()
        proposal = self.proposal()
        proposal.normalized_arguments["app_id"] = "act_something_else"
        with self.launched() as fake, self.assertRaises(AppLaunchError) as raised:
            self.service.execute(proposal)
        fake.assert_not_called()
        self.assertEqual(raised.exception.code, "digest_mismatch")

    def test_unrelated_tool_name_is_refused(self):
        self.allow()
        proposal = self.proposal()
        proposal.tool_name = "write_file"
        with self.assertRaises(NotExecutedError):
            self.service.execute(proposal)

    def test_precheck_has_no_global_workspace_to_compare(self):
        self.allow()
        self.assertEqual(self.service.precheck(self.proposal(), None), ("", ""))

    def test_preview_names_the_target_without_launching(self):
        self.allow()
        value = self.service.preview(self.proposal())
        self.assertEqual(value["change"], "launch")
        self.assertEqual(value["target_path"], str(self.exe))
        self.assertEqual(value["error"], "")
        self.assertEqual(value["diff"], [])

    def test_preview_explains_why_it_is_unavailable(self):
        # 确认卡上的预览读的是"现在"，不是提案冻下的那一刻：排队期间被撤销，预览就要说
        # 实话，否则用户确认的是一个已经不存在的东西。
        self.allow()
        proposal = self.proposal()
        self.access.revoke_grant(self.access.find_app_grant(str(self.exe)).grant_id)
        value = self.service.preview(proposal)
        self.assertEqual(value["change"], "unavailable")
        self.assertIn("已撤销", value["error"])

    def test_launchable_list_follows_the_grants(self):
        other = self.root / "other.exe"
        other.write_bytes(b"MZ\x00\x00" + bytes(8))
        self.allow(self.exe, name="演示")
        self.catalog.register_manual("另一个", str(other))
        self.assertEqual([row["name"] for row in self.service.launchable()], ["演示"])
        self.assertEqual(self.service.launchable_names(), ["演示"])
        # 撤销之后清单里就该消失：工具描述与设置页读的是同一份。
        self.access.revoke_grant(self.access.find_app_grant(str(self.exe)).grant_id)
        self.assertEqual(self.service.launchable(), [])

    def test_launchable_list_survives_a_missing_catalog_row(self):
        app_id = self.allow()
        grant = self.access.find_app_grant(str(self.exe))
        with self.store._connection() as connection:
            connection.execute("DELETE FROM app_catalog WHERE app_id = ?", (app_id,))
        self.assertEqual(self.service.launchable(), [])
        self.assertEqual(
            self.access.find_app_grant(str(self.exe)).grant_id, grant.grant_id
        )


class GrantStub:
    """open_path 只用到 canonical_root 与 grant_id 两件事。"""

    status = "active"

    def __init__(self, root=""):
        self.grant_id = "grant_stub"
        self.canonical_root = str(root)


class StubIndex:
    """只顶上 open_path 需要的那一个方法，不去建真的索引。

    resolve_reference 的返回形状（行、授权、理由）就是它的契约；这里换个假的授权对象，
    用例关心的只有"引用能不能解析、后缀挡不挡得住"。
    """

    def __init__(self, row=None, reason="", root=""):
        self.row = row
        self.reason = reason
        self.root = root
        self.calls = []

    def resolve_reference(self, file_ref, capability=None):
        self.calls.append((file_ref, capability))
        if self.row is None:
            return None, None, self.reason
        return self.row, GrantStub(self.root), ""


class OpenPathTests(unittest.TestCase):
    """``open_path`` 挂的是读取那一档，所以可执行类型必须在这一档里就挡住。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = CompanionStore(self.root / "companion.db")
        self.catalog = AppCatalogService(self.store)
        self.access = AccessControlService(self.store)
        self.service = AppLaunchService(
            self.store, self.catalog, self.access, StubIndex()
        )

    def row(self, name="report.md", rel_path="docs/report.md"):
        target = self.root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("hello", encoding="utf-8")
        info = target.lstat()
        return {
            "file_ref": "file_1",
            "name": name,
            "rel_path": rel_path,
            "volume_id": str(info.st_dev),
            "file_id": str(info.st_ino),
        }

    def test_executable_kinds_are_refused(self):
        for name in (
            "setup.exe",
            "run.bat",
            "night.PS1",
            "book.docm",
            "sheet.xlsm",
            "startlnk.lnk",
            "panel.cpl",
        ):
            with self.subTest(name=name):
                usable, reason = openable(name)
                self.assertFalse(usable)
                self.assertIn("允许启动", reason)
        # 判断只看最后一个后缀：invoice.pdf.exe 挡得住，tool.exe.7z 挡不住——后者 shell
        # 交给归档程序，本来就不可执行。名字里带 exe 不是风险，被当成 exe 跑才是。
        for name in ("report.md", "photo.png", "notes.txt", "archive.zip", "page.html"):
            self.assertTrue(openable(name)[0], name)
        # 没有后缀就不判断，交给 shell 的关联；这仍然是文档而不是命令行。
        self.assertTrue(openable("LICENSE")[0])
        self.assertFalse(openable("")[0])

    def test_open_ref_refuses_an_unresolved_reference(self):
        self.service.index = StubIndex(reason="没找到这个文件引用。")
        with self.assertRaises(AppLaunchError) as raised:
            self.service.open_ref("file_gone")
        self.assertEqual(raised.exception.code, "file_ref_unresolved")
        self.assertIn("没找到", str(raised.exception))

    def test_open_ref_needs_an_index_to_exist(self):
        service = AppLaunchService(self.store, self.catalog, self.access)
        with self.assertRaises(AppLaunchError) as raised:
            service.open_ref("file_1")
        self.assertEqual(raised.exception.code, "index_unavailable")

    def test_open_ref_passes_the_read_capability_and_launches_the_shell(self):
        self.service.index = StubIndex(row=self.row(), root=self.root)
        with mock.patch(
            "agent_framework.companion.app_launch.shell_open", return_value=424242
        ) as shell:
            opened = self.service.open_ref("file_1")
        self.assertEqual(self.service.index.calls[0][1], "fs-read")
        self.assertEqual(opened["shell_instance"], 424242)
        self.assertEqual(opened["name"], "report.md")
        self.assertEqual(Path(shell.call_args[0][0]).name, "report.md")

    def test_open_ref_refuses_an_executable_target(self):
        exe = self.root / "evil.exe"
        exe.write_bytes(b"MZ\x00\x00")
        self.service.index = StubIndex(
            row=self.row(name="evil.exe", rel_path="docs/evil.exe")
        )
        with mock.patch(
            "agent_framework.companion.app_launch.shell_open"
        ) as shell, self.assertRaises(AppLaunchError) as raised:
            self.service.open_ref("file_1")
        shell.assert_not_called()
        self.assertEqual(raised.exception.code, "not_openable")

    def test_open_ref_refuses_a_swapped_file(self):
        row = self.row()
        # 索引记的身份故意写成不可能等于当前文件的那一对：真实卷号/inode 在这台机器上
        # 未必可读（Windows 上常见 0），比对只在两个值都有效时才做。
        row["volume_id"] = "424242"
        row["file_id"] = "424242"
        self.service.index = StubIndex(row=row, root=self.root)
        with mock.patch(
            "agent_framework.companion.app_launch.shell_open"
        ) as shell, self.assertRaises(AppLaunchError) as raised:
            self.service.open_ref("file_1")
        shell.assert_not_called()
        self.assertEqual(raised.exception.code, "identity_changed")

    def test_open_ref_refuses_a_vanished_file(self):
        self.service.index = StubIndex(row=self.row(), root=self.root)
        (self.root / "docs" / "report.md").unlink()
        with mock.patch(
            "agent_framework.companion.app_launch.shell_open"
        ) as shell, self.assertRaises(AppLaunchError) as raised:
            self.service.open_ref("file_1")
        shell.assert_not_called()
        self.assertEqual(raised.exception.code, "file_missing")


class LaunchProcessTests(unittest.TestCase):
    """起进程这一段：CreateProcessW 的返回值决定 pid，句柄必须关掉。"""

    def test_command_line_quoting_and_working_dir(self):
        self.assertEqual(
            app_launch.build_command_line(r"C:\Program Files\Demo\demo.exe", "/silent"),
            '"C:\\Program Files\\Demo\\demo.exe" /silent',
        )
        self.assertEqual(
            app_launch.build_command_line(r"C:\a.exe"), '"C:\\a.exe"'
        )
        self.assertEqual(app_launch.usable_working_dir(""), None)
        self.assertEqual(
            app_launch.usable_working_dir(r"C:\definitely-not-here-9f2a"), None
        )
        self.assertEqual(app_launch.quoted_program('  "C:\\a.exe"  '), '"C:\\a.exe"')

    def test_missing_working_dir_is_not_passed_to_the_system(self):
        # CreateProcessW 会因为一个不存在的当前目录直接失败，而起不动的是那个 exe。
        captured = {}

        class FakeKernel32:
            def CreateProcessW(self, *args):
                captured["working_dir"] = args[7]
                return 0

            def GetLastError(self):
                return 2

            def CloseHandle(self, handle):
                return 1

        with mock.patch.object(app_launch, "_configure_kernel32", lambda: FakeKernel32()):
            with self.assertRaises(NotExecutedError) as raised:
                app_launch.create_process('"C:\\nope.exe"', r"C:\\gone")
        self.assertIsNone(captured["working_dir"])
        self.assertIn("找不到这个文件", str(raised.exception))

    def test_shell_error_codes_below_the_threshold_are_not_success(self):
        class FakeShell:
            def __init__(self, value):
                self.value = value

            def ShellExecuteW(self, *args):
                return self.value

            def GetLastError(self):
                return 0

        with mock.patch.object(app_launch, "_configure_kernel32", lambda: FakeShell(5)):
            with self.assertRaises(AppLaunchError) as raised:
                app_launch.shell_open("shell:RecycleBinFolder")
        self.assertEqual(raised.exception.code, "shell_execute_failed")
        with mock.patch.object(app_launch, "_configure_kernel32", lambda: FakeShell(2)):
            with self.assertRaises(NotExecutedError):
                app_launch.shell_open("shell:RecycleBinFolder")
        with mock.patch.object(app_launch, "_configure_kernel32", lambda: FakeShell(42)):
            self.assertEqual(app_launch.shell_open("shell:RecycleBinFolder"), 42)


class _SilentLLM:
    """运行链上的这两只用例只走确认与执行，不该碰到模型。"""

    def chat(self, *args, **kwargs):
        raise AssertionError("启动用例不应调用模型")

    def stream(self, *args, **kwargs):
        raise AssertionError("启动用例不应调用模型")


class _SilentSearch:
    def search(self, query, limit=5):
        return []


class RuntimeLaunchTests(unittest.TestCase):
    """登记即授权在真实运行链上的样子：不弹确认卡，但提案、账本、回执一条不少。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = CompanionStore(self.root / "companion.db")

    def runtime(self):
        from agent_framework.companion.runtime import CompanionRuntime
        from agent_framework.companion.search import SearchService

        runtime = CompanionRuntime(
            self.store,
            _SilentLLM(),
            SearchService(_SilentSearch()),
            self.root,
            offline=True,
        )
        runtime.set_mode(AgentMode.WORK)
        return runtime

    def register(self, runtime, name="演示"):
        target = self.root / "demo.exe"
        target.write_bytes(b"MZ\x00\x00" + bytes(80))
        row = runtime.app_catalog.register_manual(name, str(target))
        runtime.access.grant_app(str(target))
        runtime.app_catalog.capture_identity(row["app_id"])
        return row["app_id"]

    def test_registered_app_is_launched_without_a_confirmation_card(self):
        runtime = self.runtime()
        self.register(runtime)
        proposal = runtime.apps.create_proposal(
            session_id=runtime.session_id,
            term="演示",
            mode=AgentMode.WORK,
        )
        runtime.store.create_proposal(proposal)
        evidence = {
            "launcher": "create_process",
            "pid": 777,
            "command_line": '"C:\\\\demo.exe"',
        }
        with mock.patch(
            "agent_framework.companion.app_launch.launch_row", return_value=evidence
        ) as fake:
            outcome = runtime.confirm_proposal(proposal.action_id)
        fake.assert_called_once()
        self.assertIn("进程号 777", outcome["answer"])
        self.assertIn("不由本应用托管", outcome["answer"])
        saved = self.store.get_receipt_for_action(proposal.action_id)
        self.assertEqual(saved.receipt_id, outcome["receipt"].receipt_id)
        self.assertEqual(saved.undo_status, "unavailable")
        self.assertEqual(
            self.store.get_proposal(proposal.action_id).status,
            ProposalStatus.COMPLETED,
        )

    def test_companion_mode_refuses_before_any_process(self):
        runtime = self.runtime()
        self.register(runtime)
        proposal = runtime.apps.create_proposal(
            session_id=runtime.session_id,
            term="演示",
            mode=AgentMode.CHAT,
        )
        runtime.store.create_proposal(proposal)
        runtime.set_mode(AgentMode.CHAT)
        with mock.patch(
            "agent_framework.companion.app_launch.launch_row"
        ) as fake:
            outcome = runtime.confirm_proposal(proposal.action_id)
        fake.assert_not_called()
        self.assertIn("权限档位", outcome["answer"])
        # 拒绝也要留账：状态是"未执行"，不是失败，也没有可以撤销的东西。
        saved = self.store.get_receipt_for_action(proposal.action_id)
        self.assertEqual(saved.status, "not_executed")
        self.assertFalse(saved.undoable)

    def test_conservative_tier_hides_the_launch_permission_entirely(self):
        runtime = self.runtime()
        self.register(runtime)
        runtime.tool_permission = PERMISSION_STRICT
        self.assertNotIn("open_app", runtime.tool_names(AgentMode.WORK))
        proposal = runtime.apps.create_proposal(
            session_id=runtime.session_id,
            term="演示",
            mode=AgentMode.WORK,
        )
        runtime.store.create_proposal(proposal)
        with mock.patch(
            "agent_framework.companion.app_launch.launch_row"
        ) as fake:
            runtime.confirm_proposal(proposal.action_id)
        fake.assert_not_called()

    def test_open_path_tool_opens_through_the_shell(self):
        runtime = self.runtime()
        grant = runtime.access.grant_directory(self.root, ["fs-read"])
        note = self.root / "a.md"
        note.write_text("hello", encoding="utf-8")
        row = {
            "file_ref": "file_a",
            "name": "a.md",
            "rel_path": "a.md",
            "grant_id": grant.grant_id,
            "volume_id": "",
            "file_id": "",
        }
        with mock.patch.object(
            runtime.apps.index, "resolve_reference", return_value=(row, grant, "")
        ):
            with mock.patch(
                "agent_framework.companion.app_launch.shell_open", return_value=42
            ) as shell:
                result = runtime._tool_open_path({"file_ref": "file_a"})
        self.assertTrue(result.ok)
        shell.assert_called_once()
        self.assertIn("a.md", result.content)
        self.assertEqual(
            runtime._tool_open_path({}).code, "invalid_arguments"
        )

    def test_open_app_tool_reports_the_reason_instead_of_a_proposal(self):
        runtime = self.runtime()
        result = runtime._tool_open_app({"app": "没登记的东西"}, AgentMode.WORK, None)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "app_not_found")
        self.assertEqual(
            runtime._tool_open_app({}, AgentMode.WORK, None).code,
            "invalid_arguments",
        )


class LaunchStateReportTests(unittest.TestCase):
    """doctor 读的那份只读报告：不扫描、不算身份、不写库。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = CompanionStore(self.root / "companion.db")
        self.catalog = AppCatalogService(self.store)
        self.access = AccessControlService(self.store)
        self.exe = self.root / "demo.exe"
        self.exe.write_bytes(b"MZ\x00\x00" + bytes(80))

    def report(self):
        return app_launch.launch_state_report(self.root / "companion.db")

    def test_empty_database_reports_what_is_missing(self):
        value = self.report()
        self.assertTrue(value["available"])
        self.assertEqual(value["launchable_count"], 0)
        self.assertEqual(value["grant_count"], 0)

    def test_a_registered_and_granted_app_shows_up_as_launchable(self):
        self.catalog.register_manual("演示", str(self.exe))
        row = self.store.find_app_catalog(str(self.exe))
        self.access.grant_app(str(self.exe))
        self.catalog.capture_identity(row["app_id"])
        value = self.report()
        self.assertEqual(value["launchable_count"], 1)
        self.assertEqual(value["grant_count"], 1)
        self.assertEqual(value["launchable"][0]["name"], "演示")
        self.assertTrue(value["launchable"][0]["has_baseline"])
        self.assertEqual(value["without_baseline"], 0)

    def test_discovered_without_grant_is_counted_but_not_launchable(self):
        self.catalog.scan(links=[], app_paths=[("demo.exe", str(self.exe), "")])
        value = self.report()
        # 扫描还会带进特殊位置那几条，所以这里只断言"发现的都进了总数，但一条都不能启动"。
        self.assertGreaterEqual(value["total"], 1)
        self.assertEqual(value["launchable_count"], 0)
        self.assertEqual(value["grant_count"], 0)

    def test_drifted_rows_are_named_with_their_reason(self):
        app_id = self.allow()
        self.exe.write_bytes(b"MZ\x00\x00" + bytes(90))
        app_launch.AppLaunchService(
            self.store, self.catalog, self.access
        ).decide(self.store.get_app_catalog(app_id))
        value = self.report()
        self.assertEqual(value["drifted"], 1)
        self.assertEqual(value["launchable_count"], 0)
        self.assertIn("重新登记", value["drifted_rows"][0]["reason"])

    def test_revoked_grant_leaves_the_launchable_list(self):
        self.allow()
        self.access.revoke_grant(self.access.find_app_grant(str(self.exe)).grant_id)
        value = self.report()
        self.assertEqual(value["launchable_count"], 0)
        self.assertEqual(value["grant_count"], 0)

    def test_a_row_granted_without_a_baseline_is_flagged(self):
        # 扫描出来的行本来就没有基线（发现不算登记）；这一档要说清楚"要重新登记"。
        self.catalog.register_manual("演示", str(self.exe))
        self.access.grant_app(str(self.exe))
        value = self.report()
        self.assertEqual(value["launchable_count"], 1)
        self.assertEqual(value["without_baseline"], 1)

    def allow(self):
        self.catalog.register_manual("演示", str(self.exe))
        row = self.store.find_app_catalog(str(self.exe))
        self.access.grant_app(row["target"])
        self.catalog.capture_identity(row["app_id"])
        return row["app_id"]


if __name__ == "__main__":
    unittest.main()
