#!/usr/bin/env python3
"""WebAppFlaskscrcpy 数据库初始化 / 维护工具。

驱动 ``services.database`` 这一层（原生 sqlite3，非 SQLAlchemy），只操作账号 /
会话 / 设备占用三张表，刻意不导入 ``app.py``，以免触发 adb bootstrap、
后台线程等副作用——本工具应当能在不启动整套服务的情况下独立运行。

用法
----
    # 初始化数据库（建表 + 写入预设账号；幂等）
    python scripts/init_db.py init

    # 清空运行态数据：会话 + 设备占用 + 已注册的普通用户（保留预设账号与表结构）
    python scripts/init_db.py clear

    # 重置数据库（删除所有表后重建并重新写入预设账号；危险操作）
    python scripts/init_db.py reset

    # 查看数据库状态（账号、占用、文件大小）
    python scripts/init_db.py status

    # 备份数据库文件
    python scripts/init_db.py backup
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

# Windows 控制台默认非 UTF-8，中文输出会乱码——强制 UTF-8 编码。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# 把项目根目录加入 import 路径，使脚本可从任意 cwd 运行。
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from services import database as db_module  # noqa: E402

# 预设账号用户名集合——clear 时据此保留 super_admin / admin。
SEED_USERNAMES = {row[0] for row in db_module.SEED_ACCOUNTS}


def _db_exists() -> bool:
    return db_module.DB_PATH.exists()


def _tables_exist(conn) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    return row is not None


# ── 命令实现 ──────────────────────────────────────────────────────────────

def init_database() -> None:
    """建表并写入预设账号。委托给 services.database.init_db（幂等）。"""
    print("开始初始化数据库...")
    db_module.init_db()
    print(f"数据库初始化完成: {db_module.DB_PATH}")
    show_database_status()


def clear_database() -> None:
    """清空运行态数据：会话、设备占用、已注册普通用户（保留预设账号）。"""
    if not _db_exists():
        print("数据库尚未初始化，无需清空。先运行 `init`。")
        return
    conn = db_module.get_conn()
    try:
        if not _tables_exist(conn):
            print("表结构不存在，无需清空。先运行 `init`。")
            return
        placeholders = ",".join("?" * len(SEED_USERNAMES))
        sessions = conn.execute("DELETE FROM user_sessions").rowcount
        reservations = conn.execute("DELETE FROM device_reservations").rowcount
        users = conn.execute(
            f"DELETE FROM users WHERE username NOT IN ({placeholders})",
            tuple(SEED_USERNAMES),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    print("=" * 56)
    print("数据库运行态数据已清空（预设账号与表结构保留）")
    print("=" * 56)
    print(f"  user_sessions       : {sessions} 条")
    print(f"  device_reservations : {reservations} 条")
    print(f"  users (非预设)       : {users} 条")
    print("=" * 56)


def reset_database() -> None:
    """删除所有表后重建并重新写入预设账号。"""
    print("开始重置数据库...")
    if _db_exists():
        conn = db_module.get_conn()
        try:
            conn.executescript(
                """
                DROP TABLE IF EXISTS user_sessions;
                DROP TABLE IF EXISTS device_reservations;
                DROP TABLE IF EXISTS users;
                """
            )
            conn.commit()
        finally:
            conn.close()
        print("已删除所有数据表")
    # 复位模块级幂等开关，确保 init_db 真正重建。
    db_module._initialised = False
    db_module.init_db()
    print("已重新创建数据表并写入预设账号")
    show_database_status()


def backup_database() -> str | None:
    """复制数据库文件，返回备份路径。"""
    if not _db_exists():
        print("数据库文件不存在，无法备份。")
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_module.DB_PATH.with_name(f"{db_module.DB_PATH.name}.backup_{timestamp}")
    shutil.copy2(db_module.DB_PATH, backup_path)
    print(f"数据库备份完成: {backup_path}")
    return str(backup_path)


def show_database_status() -> None:
    """打印账号、设备占用与数据库文件信息。"""
    print("=" * 56)
    print("数据库状态报告")
    print("=" * 56)
    if not _db_exists():
        print(f"数据库未初始化（文件不存在）: {db_module.DB_PATH}")
        print("=" * 56)
        return

    conn = db_module.get_conn()
    try:
        if not _tables_exist(conn):
            print("数据库文件存在但表结构缺失，请运行 `init`。")
            print("=" * 56)
            return

        users = conn.execute(
            "SELECT username, email, role, is_active FROM users ORDER BY id"
        ).fetchall()
        sessions_count = conn.execute(
            "SELECT COUNT(*) AS c FROM user_sessions"
        ).fetchone()["c"]
        reservations = conn.execute(
            "SELECT device_id, username, expires_at FROM device_reservations "
            "ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()

    print(f"账号总数: {len(users)}")
    for u in users:
        state = "启用" if u["is_active"] else "停用"
        print(f"  - {u['username']:<14} {u['role']:<12} {state:<4} {u['email']}")
    print()
    print(f"活动会话数: {sessions_count}")
    print(f"设备占用数: {len(reservations)}")
    for r in reservations:
        print(f"  - {r['device_id']:<24} 占用者={r['username']:<14} 到期={r['expires_at']}")
    print()

    size = db_module.DB_PATH.stat().st_size
    print(f"数据库文件: {db_module.DB_PATH}")
    print(f"文件大小: {size / 1024:.2f} KB")
    print("=" * 56)


# ── CLI ─────────────────────────────────────────────────────────────────

def _print_help() -> None:
    print("可用命令:")
    print("  init    - 初始化数据库（建表 + 预设账号，幂等）")
    print("  clear   - 清空运行态数据（会话/占用/已注册用户，保留预设账号）")
    print("  reset   - 重置数据库（删除所有表并重建，危险操作）")
    print("  status  - 显示数据库状态")
    print("  backup  - 备份数据库文件")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "init"

    if command == "init":
        init_database()
    elif command == "clear":
        confirm = input("确定清空运行态数据吗？（会话/占用/已注册用户将被删除）(y/N): ")
        if confirm.lower() == "y":
            clear_database()
        else:
            print("操作已取消")
    elif command == "reset":
        confirm = input("确定重置数据库吗？这将删除包括账号在内的所有数据！(y/N): ")
        if confirm.lower() == "y":
            reset_database()
        else:
            print("操作已取消")
    elif command == "status":
        show_database_status()
    elif command == "backup":
        backup_database()
    else:
        print(f"未知命令: {command}\n")
        _print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
