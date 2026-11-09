"""mcmod-studio command line interface."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .audit import audit, summarize as summarize_audit
from .build import build, summarize as summarize_build
from .doctor import doctor, summarize as summarize_doctor
from .package import package as package_project, summarize as summarize_package
from .prompt import stage_prompt
from .recon import api_search, build_card, check_imports, load_card, summarize as summarize_recon
from .scaffold import add_slice, scaffold
from .spec import build_spec, spec_to_markdown
from .util import McmodError, read_json, write_json, write_text
from .verify import verify_client, verify_gametest, verify_server, summarize as summarize_verify
from .versions import LOADERS, matrix, profile_to_markdown, resolve, suggestions


def _project(path: str | None) -> Path:
    return Path(path or ".").resolve()


def _profile_for(project: Path):
    state = read_json(project / ".mcmod" / "state.json", {}) or {}
    data = state.get("profile")
    if not data:
        raise McmodError("项目缺少 .mcmod/state.json 中的版本信息")
    from .versions import VersionProfile
    return VersionProfile(**data)


def _spec_for(project: Path) -> dict[str, Any]:
    spec = read_json(project / "mod-spec.json")
    if not spec:
        raise McmodError(f"找不到 {project / 'mod-spec.json'}")
    return spec


def cmd_doctor(args: argparse.Namespace) -> int:
    report = doctor(Path(args.project) if args.project else None)
    print(summarize_doctor(report))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_versions(args: argparse.Namespace) -> int:
    if args.matrix:
        chosen = args.loader or list(LOADERS)
        rows = matrix(loaders=chosen, limit=args.limit, refresh=args.refresh)
        headers = ["mc", "java", *chosen, *[f"{loader}_tier" for loader in chosen]]
        print(" | ".join(headers))
        for row in rows:
            print(" | ".join(str(row.get(h, "")) for h in headers))
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    loaders = args.loader or list(LOADERS)
    out = []
    for loader in loaders:
        try:
            profile = resolve(loader, args.mc, refresh=args.refresh)
            out.append(profile.to_dict())
            print(profile_to_markdown(profile))
            if not profile.supported:
                sug = suggestions(loader, profile.mc)
                print(f"可用加载器：{sug['loaders']}")
                print(f"邻近版本：{sug['mc']}")
        except McmodError as exc:
            print(f"{loader}: {exc}", file=sys.stderr)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    spec = build_spec(
        args.idea, args.loader, args.mc or resolve(args.loader).mc,
        name=args.name, modid=args.modid, package=args.package,
        scope=args.scope, author=args.author, license=args.license,
    )
    out = Path(args.out) if args.out else Path.cwd() / (spec["identity"]["modid"] + "-design")
    out = out.resolve()
    write_json(out / "mod-spec.json", spec)
    md = spec_to_markdown(spec)
    write_text(out / "GDD.md", md)
    print(f"构思已生成：{out}")
    print(f"- 内容节点：{len(spec['content'])}")
    print(f"- 主题：{', '.join(spec['themes'])}")
    print(f"- Mod ID：{spec['identity']['modid']}")
    if args.print or args.stdout:
        print(md)
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    if args.spec:
        spec = read_json(Path(args.spec))
        if not spec:
            raise McmodError(f"无法读取 spec：{args.spec}")
        loader = args.loader or spec["target"]["loader"]
        mc = args.mc or spec["target"]["mc"]
    else:
        if not args.idea:
            raise McmodError("需要 --idea 或 --spec")
        loader = args.loader or "neoforge"
        mc = args.mc or resolve(loader).mc
        spec = build_spec(
            args.idea, loader, mc, name=args.name, modid=args.modid,
            package=args.package, scope=args.scope, author=args.author,
            license=args.license,
        )
    profile = resolve(loader, mc, refresh=args.refresh)
    if not profile.supported and not args.force_unsupported:
        print(f"目标组合不受支持：{loader} {mc}。", file=sys.stderr)
        sug = suggestions(loader, mc)
        print(f"邻近可用版本：{sug['mc']}", file=sys.stderr)
        print(f"可用加载器：{sug['loaders']}", file=sys.stderr)
        return 2
    project = Path(args.dir).resolve()
    result = scaffold(project, spec, profile, use_mdk=not args.no_mdk, force=args.force)
    print(f"工程已生成：{project}")
    print(f"- 模式：{result['mode']}")
    print(f"- 资源：{result['assets']}")
    for warning in result["warnings"]:
        print(f"- 警告：{warning}")
    print("\n下一步：")
    print(f"  mcmod audit {project}")
    print(f"  mcmod build {project} --fix")
    print(f"  mcmod verify server {project}")
    print(f"  mcmod verify client {project}")
    if args.build:
        report = build(project, profile, fix=True, timeout=args.timeout)
        print(summarize_build(report))
    return 0


def cmd_slice(args: argparse.Namespace) -> int:
    project = _project(args.project)
    if args.action != "add":
        raise McmodError("只支持 `slice add`")
    node = add_slice(project, args.type, args.id, args.name_zh or "", args.name_en or "")
    print(f"已添加：{node['id']} ({node['type']})")
    print("下一步：mcmod audit . && mcmod build . --fix")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    project = _project(args.project)
    profile = None
    try:
        profile = _profile_for(project)
    except McmodError:
        pass
    report = audit(project, profile)
    print(summarize_audit(report))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def cmd_build(args: argparse.Namespace) -> int:
    project = _project(args.project)
    profile = _profile_for(project)
    report = build(project, profile, timeout=args.timeout, fix=args.fix,
                   max_iterations=args.iterations, task=args.task)
    print(summarize_build(report))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def cmd_verify(args: argparse.Namespace) -> int:
    project = _project(args.project)
    profile = _profile_for(project)
    mode = args.mode
    results = []
    if mode in {"server", "all"}:
        results.append(verify_server(project, profile, timeout=args.timeout))
    if mode in {"gametest", "all"}:
        results.append(verify_gametest(project, profile, timeout=args.timeout))
    if mode in {"client", "all"}:
        results.append(verify_client(project, profile, wait_seconds=args.wait,
                                     timeout=args.timeout, quick_play=args.quick_play or ""))
    for result in results:
        print(summarize_verify(result))
    ok = all(r.get("ok") or r.get("skipped") for r in results)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def cmd_package(args: argparse.Namespace) -> int:
    project = _project(args.project)
    profile = _profile_for(project)
    spec = _spec_for(project)
    report = package_project(project, spec, profile)
    print(summarize_package(report))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def cmd_recon(args: argparse.Namespace) -> int:
    project = _project(args.project)
    profile = _profile_for(project)
    card = build_card(project, profile, jar=Path(args.jar) if args.jar else None,
                      gen_sources=not args.no_gen)
    print(summarize_recon(card))
    return 0


def cmd_api_search(args: argparse.Namespace) -> int:
    project = _project(args.project)
    results = api_search(project, args.query, limit=args.limit)
    if not results:
        print("没有匹配。")
        return 1
    print("\n".join(results))
    return 0


def cmd_check_imports(args: argparse.Namespace) -> int:
    project = _project(args.project)
    report = check_imports(project)
    print(f"检查 {report['checked']} 个 API import，问题 {len(report['issues'])} 个。")
    for issue in report["issues"][:40]:
        print(f"- {issue['file']}: {issue['import']}")
        print(f"  {issue['message']}")
    return 0 if report["ok"] else 1


def cmd_prompt(args: argparse.Namespace) -> int:
    project = _project(args.project)
    print(stage_prompt(project, args.stage))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    project = _project(args.project)
    created = False
    if not (project / "mod-spec.json").exists():
        if not args.idea:
            raise McmodError("项目没有 mod-spec.json；需要 --idea 来创建")
        loader = args.loader
        mc = args.mc or resolve(loader).mc
        spec = build_spec(args.idea, loader, mc, name=args.name, modid=args.modid,
                          package=args.package, scope=args.scope, author=args.author,
                          license=args.license)
        profile = resolve(loader, mc, refresh=args.refresh)
        if not profile.supported:
            print(f"目标组合不受支持：{loader} {mc}", file=sys.stderr)
            return 2
        scaffold(project, spec, profile, use_mdk=not args.no_mdk, force=args.force)
        created = True
        print(f"已创建工程：{project}")
    profile = _profile_for(project)
    spec = _spec_for(project)

    stages: list[tuple[str, bool]] = []
    audit_report = audit(project, profile)
    print("\n== G2 资源审计 ==")
    print(summarize_audit(audit_report))
    stages.append(("audit", audit_report["ok"]))
    if not audit_report["ok"] and args.strict_audit:
        print("审计未通过，停止。")
        return 1

    if not args.no_build:
        print("\n== G1 编译 ==")
        build_report = build(project, profile, timeout=args.timeout, fix=True,
                             max_iterations=args.iterations)
        print(summarize_build(build_report))
        stages.append(("build", build_report["ok"]))
        if not build_report["ok"]:
            print("构建失败，停止。")
            return 1

        if not args.no_server:
            print("\n== G4 服务端冒烟 ==")
            server = verify_server(project, profile, timeout=args.verify_timeout)
            print(summarize_verify(server))
            stages.append(("server", server["ok"]))
        if not args.no_client:
            print("\n== G5 客户端冒烟 ==")
            client = verify_client(project, profile, wait_seconds=args.wait,
                                   timeout=args.verify_timeout,
                                   quick_play=args.quick_play or "")
            print(summarize_verify(client))
            stages.append(("client", client["ok"]))

        print("\n== G7 打包 ==")
        package_report = package_project(project, spec, profile)
        print(summarize_package(package_report))
        stages.append(("package", package_report["ok"]))

    print("\n== 流水线结果 ==")
    for name, ok in stages:
        print(f"- {name}: {'PASS' if ok else 'FAIL'}")
    return 0 if all(ok for _, ok in stages) else 1


def cmd_install_skill(args: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parent.parent
    target = Path(args.target).expanduser() if args.target else _default_skill_target()
    target.mkdir(parents=True, exist_ok=True)
    for name in ["SKILL.md", "launch.py", "mcmod.cmd", "bin",
                 "mcmod", "references", "templates", "scripts"]:
        src = root / name
        if not src.exists():
            continue
        dst = target / name
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dst)
    print(f"Skill 已安装到：{target}")
    print("把该目录加入你的 Skill 搜索路径，或在客户端中重新加载 skills。")
    return 0


def _default_skill_target() -> Path:
    candidates = [
        Path.home() / ".claude" / "skills" / "mcmod-studio",
        Path.home() / ".qoder" / "skills" / "mcmod-studio",
        Path.home() / ".tac" / "skills" / "mcmod-studio",
        Path.home() / "Tianxi" / "skills" / "mcmod-studio",
    ]
    for candidate in candidates:
        parent = candidate.parent
        if parent.exists():
            return candidate
    return Path.home() / ".mcmod" / "skill" / "mcmod-studio"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcmod",
        description="Minecraft mod studio: spec → scaffold → audit → build → verify → package",
    )
    parser.add_argument("--version", action="version", version=f"mcmod-studio {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="检查 JDK、Gradle、网络与磁盘")
    p.add_argument("project", nargs="?")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("versions", help="解析加载器/Minecraft 版本")
    p.add_argument("--loader", action="append", choices=list(LOADERS))
    p.add_argument("--mc")
    p.add_argument("--matrix", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_versions)

    p = sub.add_parser("plan", help="从一个点子生成完整构思（GDD + mod-spec）")
    p.add_argument("idea")
    p.add_argument("--loader", default="neoforge", choices=list(LOADERS))
    p.add_argument("--mc")
    p.add_argument("--name")
    p.add_argument("--modid")
    p.add_argument("--package")
    p.add_argument("--scope", default="medium", choices=["small", "medium", "large", "epic"])
    p.add_argument("--author", default="Your Name")
    p.add_argument("--license", default="MIT")
    p.add_argument("--out")
    p.add_argument("--print", action="store_true")
    p.add_argument("--stdout", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("new", help="生成可构建工程")
    p.add_argument("dir")
    p.add_argument("--idea")
    p.add_argument("--spec")
    p.add_argument("--loader", choices=list(LOADERS))
    p.add_argument("--mc")
    p.add_argument("--name")
    p.add_argument("--modid")
    p.add_argument("--package")
    p.add_argument("--scope", default="medium", choices=["small", "medium", "large", "epic"])
    p.add_argument("--author", default="Your Name")
    p.add_argument("--license", default="MIT")
    p.add_argument("--no-mdk", action="store_true", help="不下载官方 MDK，使用内置模板")
    p.add_argument("--force", action="store_true")
    p.add_argument("--force-unsupported", action="store_true")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--build", action="store_true")
    p.add_argument("--timeout", type=int, default=1800)
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("slice", help="增量添加一个内容切片")
    p.add_argument("action", choices=["add"])
    p.add_argument("type")
    p.add_argument("id")
    p.add_argument("--project", default=".")
    p.add_argument("--name-zh")
    p.add_argument("--name-en")
    p.set_defaults(func=cmd_slice)

    p = sub.add_parser("audit", help="审计资源/注册/语言完整性")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("build", help="Gradle 构建与诊断")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--fix", action="store_true", help="应用高置信度机械修复并重试")
    p.add_argument("--iterations", type=int, default=4)
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--task", default="build")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("verify", help="启动游戏自动验证")
    p.add_argument("mode", choices=["server", "gametest", "client", "all"])
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--wait", type=int, default=50, help="客户端进入标题后等待秒数")
    p.add_argument("--quick-play", help="直连地址，例如 127.0.0.1:25565")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("package", help="收集 jar 并生成发布元数据")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_package)

    p = sub.add_parser("recon", help="从 sources jar 生成 API 卡")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--jar")
    p.add_argument("--no-gen", action="store_true")
    p.set_defaults(func=cmd_recon)

    p = sub.add_parser("api-search", help="在 API 卡中搜索类/方法")
    p.add_argument("query")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_api_search)

    p = sub.add_parser("check-imports", help="拦截 API 卡中不存在的 import")
    p.add_argument("project", nargs="?", default=".")
    p.set_defaults(func=cmd_check_imports)

    p = sub.add_parser("prompt", help="输出阶段提示词包")
    p.add_argument("stage", choices=["design", "code", "asset", "qa"])
    p.add_argument("project", nargs="?", default=".")
    p.set_defaults(func=cmd_prompt)

    p = sub.add_parser("run", help="端到端流水线")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--idea")
    p.add_argument("--loader", default="neoforge", choices=list(LOADERS))
    p.add_argument("--mc")
    p.add_argument("--name")
    p.add_argument("--modid")
    p.add_argument("--package")
    p.add_argument("--scope", default="medium", choices=["small", "medium", "large", "epic"])
    p.add_argument("--author", default="Your Name")
    p.add_argument("--license", default="MIT")
    p.add_argument("--no-mdk", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-build", action="store_true")
    p.add_argument("--no-server", action="store_true")
    p.add_argument("--no-client", action="store_true")
    p.add_argument("--strict-audit", action="store_true")
    p.add_argument("--iterations", type=int, default=4)
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--verify-timeout", type=int, default=600)
    p.add_argument("--wait", type=int, default=50)
    p.add_argument("--quick-play")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("install-skill", help="把 Skill 安装到本机 skills 目录")
    p.add_argument("--target")
    p.set_defaults(func=cmd_install_skill)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except McmodError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
