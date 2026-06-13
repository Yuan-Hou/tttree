"""一次性幂等迁移:给现有 vore.db 的 turns 表补上 M4.5-B 的三列。

背景:无迁移框架的单人本地工具;create_all 只建缺失的表、不补缺失的列。已存在的 turns 表
是在 M4.5-B 之前建的,缺 director_a_messages / writer_messages / director_b_messages 三列。
本脚本检查并用 ALTER TABLE ADD COLUMN 补齐(已存在则跳过),让旧库能跑新代码。

用法(backend/ 下):  python -m scripts.migrate_step_contexts
"""

import sqlite3

from app.db.session import DB_PATH

NEW_COLUMNS = ("director_a_messages", "writer_messages", "director_b_messages")


def main() -> None:
    if not DB_PATH.exists():
        print(f"{DB_PATH} 不存在,无需迁移(下次启动 create_all 会建全 schema)。")
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(turns)")}
        if not existing:
            print("turns 表不存在,无需迁移。")
            return
        added = []
        for col in NEW_COLUMNS:
            if col not in existing:
                conn.execute(f"ALTER TABLE turns ADD COLUMN {col} TEXT DEFAULT ''")
                added.append(col)
        conn.commit()
        print(f"已补列: {added}" if added else "三列均已存在,无需改动。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
