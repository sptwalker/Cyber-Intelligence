# -*- coding: utf-8 -*-
"""关键词库管理：CRUD + 标签体系

支持8种标签：
- product_name: 产品名（主体实体）
- slogan: 口号
- promo: 宣传语
- complaint: 吐槽点（负面关键词）
- selling_point: 卖点（正面关键词）
- competitor: 竞品
- related: 相关
- similar: 近似（同义词）
"""

from __future__ import annotations
import sqlite3
from datetime import datetime
from typing import Optional

# 标签定义
TAGS = {
    'product_name': '产品名',
    'slogan': '口号',
    'promo': '宣传语',
    'complaint': '吐槽点',
    'selling_point': '卖点',
    'competitor': '竞品',
    'related': '相关',
    'similar': '近似',
}

# 多维标注枚举（唯一事实源，Phase A 冻结，A/B/C/E 全部 import 同一份，防跨组件枚举串味）。
SUBJECTS = ["官方", "准官方", "媒体", "用户·KOL"]                    # 主体：谁在说
STANCES = ["赞扬", "中立", "批评", "吐槽", "投诉", "纯传播"]         # 立场：怎么说
IMPORTANCE = ["高", "中", "低"]                                      # 重要性（由声量当量派生，可人工改）


class KeywordManager:
    """关键词库管理器"""

    def __init__(self, store):
        """
        Args:
            store: Store实例（复用现有数据库连接）
        """
        self.store = store
        self.conn = store.conn
        self._ensure_tables()

    def _ensure_tables(self):
        """确保keywords和keyword_suggestions表存在"""
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                word TEXT NOT NULL,
                tag TEXT NOT NULL,
                entity_id TEXT,
                weight REAL DEFAULT 1.0,
                source TEXT DEFAULT 'manual',
                note TEXT,
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(word, tag, entity_id)
            )
        ''')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_keywords_tag ON keywords(tag)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_keywords_entity ON keywords(entity_id)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_keywords_word ON keywords(word)')

        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS keyword_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                word TEXT NOT NULL,
                suggested_tag TEXT NOT NULL,
                entity_id TEXT,
                score REAL,
                reason TEXT,
                source_docs TEXT,
                status TEXT DEFAULT 'pending',
                suggested_at TEXT,
                reviewed_at TEXT,
                UNIQUE(word, suggested_tag, entity_id)
            )
        ''')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_suggestions_status ON keyword_suggestions(status)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_suggestions_entity ON keyword_suggestions(entity_id)')
        self.conn.commit()

    # ========== CRUD操作 ==========

    def add(self, word: str, tag: str, entity_id: Optional[str] = None,
            weight: float = 1.0, note: Optional[str] = None, source: str = 'manual') -> int:
        """添加关键词

        Args:
            word: 关键词
            tag: 标签（product_name/complaint等）
            entity_id: 关联的监控实体（可选）
            weight: 权重（0-1，影响优先级）
            note: 备注
            source: 来源（manual/auto）

        Returns:
            keyword_id

        Raises:
            ValueError: tag不合法或词已存在
        """
        if tag not in TAGS:                          # 允许自定义标签，仅做基本清洗（内置标签受 analyze/insights 逻辑约束，勿改其码）
            tag = (tag or '').strip()
            if not tag:
                raise ValueError("Tag cannot be empty")
            if len(tag) > 20:
                raise ValueError("Tag too long (max 20 chars)")

        word = word.strip()
        if not word:
            raise ValueError("Word cannot be empty")

        now = datetime.now().isoformat(timespec='seconds')
        try:
            cursor = self.conn.execute('''
                INSERT INTO keywords (word, tag, entity_id, weight, source, note, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (word, tag, entity_id, weight, source, note, now, now))
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError(f"Keyword already exists: word={word}, tag={tag}, entity_id={entity_id}")

    def remove(self, word: str, tag: str, entity_id: Optional[str] = None) -> bool:
        """删除关键词

        Returns:
            是否删除成功
        """
        cursor = self.conn.execute(
            'DELETE FROM keywords WHERE word=? AND tag=? AND entity_id IS ?',
            (word, tag, entity_id)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def update(self, word: str, tag: str, entity_id: Optional[str], **fields) -> bool:
        """更新关键词属性

        Args:
            word: 关键词
            tag: 标签
            entity_id: 实体ID
            **fields: 要更新的字段（weight/note/source）

        Returns:
            是否更新成功
        """
        allowed = {'weight', 'note', 'source'}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False

        updates['updated_at'] = datetime.now().isoformat(timespec='seconds')

        set_clause = ', '.join(f'{k}=?' for k in updates.keys())
        values = list(updates.values()) + [word, tag, entity_id]

        cursor = self.conn.execute(
            f'UPDATE keywords SET {set_clause} WHERE word=? AND tag=? AND entity_id IS ?',
            values
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get(self, word: str, tag: str, entity_id: Optional[str] = None) -> Optional[dict]:
        """获取单个关键词"""
        row = self.conn.execute(
            'SELECT * FROM keywords WHERE word=? AND tag=? AND entity_id IS ?',
            (word, tag, entity_id)
        ).fetchone()
        return dict(row) if row else None

    def list(self, tag: Optional[str] = None, entity_id: Optional[str] = None,
             source: Optional[str] = None) -> list[dict]:
        """列出关键词（可按条件筛选）

        Args:
            tag: 按标签筛选（可选）
            entity_id: 按实体筛选（可选）
            source: 按来源筛选（manual/auto）

        Returns:
            关键词列表
        """
        sql = 'SELECT * FROM keywords WHERE 1=1'
        params = []

        if tag:
            sql += ' AND tag=?'
            params.append(tag)
        if entity_id is not None:
            sql += ' AND entity_id=?'
            params.append(entity_id)
        if source:
            sql += ' AND source=?'
            params.append(source)

        sql += ' ORDER BY weight DESC, created_at DESC'

        rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    # ========== 查询接口 ==========

    def get_by_tag(self, tag: str, entity_id: Optional[str] = None, min_weight: float = 0.0) -> list[dict]:
        """获取某标签下的所有词"""
        sql = 'SELECT * FROM keywords WHERE tag=? AND weight>=?'
        params = [tag, min_weight]

        if entity_id is not None:
            sql += ' AND entity_id=?'
            params.append(entity_id)

        sql += ' ORDER BY weight DESC'

        rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_similar_words(self, word: str, entity_id: Optional[str] = None) -> list[dict]:
        """获取某词的同义词（similar标签）

        Returns:
            [{'word': '烫手', 'weight': 0.8, 'note': '→发热'}, ...]
        """
        # 查找：1) word本身标记为similar 2) note中提到word的similar词
        sql = '''
            SELECT * FROM keywords
            WHERE tag='similar'
            AND (word=? OR note LIKE '%' || ? || '%')
            AND entity_id IS ?
            ORDER BY weight DESC
        '''
        rows = self.conn.execute(sql, (word, word, entity_id)).fetchall()
        return [dict(row) for row in rows]

    def get_complaints(self, entity_id: Optional[str] = None, min_weight: float = 0.5) -> list[dict]:
        """获取所有吐槽点（complaint标签）"""
        return self.get_by_tag('complaint', entity_id, min_weight)

    def get_selling_points(self, entity_id: Optional[str] = None, min_weight: float = 0.5) -> list[dict]:
        """获取所有卖点（selling_point标签）"""
        return self.get_by_tag('selling_point', entity_id, min_weight)

    def get_competitors(self, entity_id: Optional[str] = None) -> list[dict]:
        """获取竞品列表"""
        return self.get_by_tag('competitor', entity_id)

    # ========== AI推荐管理 ==========

    def add_suggestion(self, word: str, suggested_tag: str, entity_id: Optional[str] = None,
                      score: float = 0.0, reason: str = '', source_docs: str = '') -> int:
        """添加AI推荐词

        Args:
            word: 推荐的词
            suggested_tag: 建议的标签
            entity_id: 实体ID
            score: 推荐置信度（0-1）
            reason: 推荐理由
            source_docs: 来源doc_ids（JSON数组字符串）

        Returns:
            suggestion_id
        """
        now = datetime.now().isoformat(timespec='seconds')
        try:
            cursor = self.conn.execute('''
                INSERT INTO keyword_suggestions
                (word, suggested_tag, entity_id, score, reason, source_docs, status, suggested_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            ''', (word, suggested_tag, entity_id, score, reason, source_docs, now))
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            # 已存在相同推荐，更新score和reason
            self.conn.execute('''
                UPDATE keyword_suggestions
                SET score=?, reason=?, source_docs=?, suggested_at=?
                WHERE word=? AND suggested_tag=? AND entity_id IS ? AND status='pending'
            ''', (score, reason, source_docs, now, word, suggested_tag, entity_id))
            self.conn.commit()
            return 0

    def list_suggestions(self, status: str = 'pending', entity_id: Optional[str] = None,
                         tag: Optional[str] = None, exclude_tag: Optional[str] = None) -> list[dict]:
        """列出推荐词。tag=只取该建议标签；exclude_tag=排除某标签（如 /keywords 排除 seed_alias）。"""
        sql = 'SELECT * FROM keyword_suggestions WHERE status=?'
        params = [status]

        if entity_id is not None:
            sql += ' AND entity_id=?'
            params.append(entity_id)
        if tag is not None:
            sql += ' AND suggested_tag=?'
            params.append(tag)
        if exclude_tag is not None:
            sql += ' AND suggested_tag<>?'
            params.append(exclude_tag)

        sql += ' ORDER BY score DESC, suggested_at DESC'

        rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def mark_suggestion(self, suggestion_id: int, status: str) -> bool:
        """仅更新建议状态（不写关键词库）。供种子建议 approve（词已写 watch.yaml）用。"""
        now = datetime.now().isoformat(timespec='seconds')
        cur = self.conn.execute(
            'UPDATE keyword_suggestions SET status=?, reviewed_at=? WHERE id=?',
            (status, now, suggestion_id))
        self.conn.commit()
        return cur.rowcount > 0

    def approve_suggestion(self, suggestion_id: int) -> bool:
        """批准推荐（添加到正式词库）

        Returns:
            是否成功
        """
        # 获取推荐
        row = self.conn.execute(
            'SELECT * FROM keyword_suggestions WHERE id=?', (suggestion_id,)
        ).fetchone()

        if not row:
            return False

        sug = dict(row)
        if sug['suggested_tag'] == 'seed_alias':      # 种子词走 watch.yaml，不进关键词库（防串味）
            return False

        # 添加到keywords表
        try:
            self.add(
                word=sug['word'],
                tag=sug['suggested_tag'],
                entity_id=sug['entity_id'],
                weight=sug['score'] or 0.8,  # 使用推荐分作为权重
                note=f"AI推荐: {sug['reason']}" if sug['reason'] else None,
                source='auto'
            )
        except ValueError:
            # 已存在，忽略
            pass

        # 更新推荐状态
        now = datetime.now().isoformat(timespec='seconds')
        self.conn.execute(
            'UPDATE keyword_suggestions SET status=?, reviewed_at=? WHERE id=?',
            ('approved', now, suggestion_id)
        )
        self.conn.commit()
        return True

    def reject_suggestion(self, suggestion_id: int, reason: Optional[str] = None) -> bool:
        """拒绝推荐

        Returns:
            是否成功
        """
        now = datetime.now().isoformat(timespec='seconds')
        note = f"拒绝原因: {reason}" if reason else None

        cursor = self.conn.execute('''
            UPDATE keyword_suggestions
            SET status=?, reviewed_at=?, reason=?
            WHERE id=? AND status='pending'
        ''', ('rejected', now, note, suggestion_id))
        self.conn.commit()
        return cursor.rowcount > 0

    # ========== 批量操作 ==========

    def bulk_add(self, keywords: list[dict]) -> tuple[int, list[str]]:
        """批量添加关键词

        Args:
            keywords: [{'word': 'xx', 'tag': 'xx', 'entity_id': 'xx', ...}, ...]

        Returns:
            (成功数量, 错误信息列表)
        """
        success = 0
        errors = []

        for kw in keywords:
            try:
                self.add(**kw)
                success += 1
            except Exception as e:
                errors.append(f"{kw.get('word')}: {e}")

        return success, errors

    def export_to_dict(self, entity_id: Optional[str] = None) -> dict:
        """导出词库为字典（用于CSV/JSON导出）

        Returns:
            {'product_name': ['星海手机', ...], 'complaint': ['发热', ...], ...}
        """
        result = {tag: [] for tag in TAGS}

        for tag in TAGS:
            words = self.get_by_tag(tag, entity_id)
            result[tag] = [w['word'] for w in words]

        return result


# ========== 单元测试 ==========

if __name__ == '__main__':
    from .store import Store

    # 测试
    s = Store(':memory:')
    km = KeywordManager(s)

    print("=== 关键词库管理测试 ===\n")

    # 1. 添加关键词
    print("1. 添加关键词")
    km.add('星海手机', 'product_name', entity_id='test')
    km.add('发热', 'complaint', entity_id='test', weight=1.0)
    km.add('烫手', 'similar', entity_id='test', weight=0.85, note='→发热')
    km.add('续航长', 'selling_point', entity_id='test', weight=0.9)
    km.add('小米', 'competitor', entity_id='test')
    print("  ✓ 已添加5个关键词\n")

    # 2. 查询
    print("2. 查询关键词")
    all_kw = km.list(entity_id='test')
    print(f"  总计: {len(all_kw)} 个")

    complaints = km.get_complaints(entity_id='test')
    print(f"  吐槽点: {[k['word'] for k in complaints]}")

    selling = km.get_selling_points(entity_id='test')
    print(f"  卖点: {[k['word'] for k in selling]}")

    similar = km.get_similar_words('发热', entity_id='test')
    print(f"  '发热'的同义词: {[k['word'] for k in similar]}\n")

    # 3. AI推荐
    print("3. AI推荐")
    km.add_suggestion('卡顿', 'complaint', entity_id='test', score=0.78, reason='负面帖出现45次')
    km.add_suggestion('死机', 'similar', entity_id='test', score=0.82, reason='与"卡顿"语义相似')

    suggestions = km.list_suggestions(status='pending', entity_id='test')
    print(f"  待审核推荐: {len(suggestions)} 条")
    for sug in suggestions:
        print(f"    - {sug['word']} → {sug['suggested_tag']} (分数{sug['score']}) {sug['reason']}")

    # 4. 审核推荐
    print("\n4. 审核推荐")
    km.approve_suggestion(suggestions[0]['id'])
    print(f"  ✓ 已批准: {suggestions[0]['word']}")

    km.reject_suggestion(suggestions[1]['id'], reason='不相关')
    print(f"  ✗ 已拒绝: {suggestions[1]['word']}\n")

    # 5. 更新和删除
    print("5. 更新和删除")
    km.update('发热', 'complaint', 'test', weight=0.95, note='高频吐槽点')
    print("  ✓ 已更新'发热'的权重")

    km.remove('小米', 'competitor', 'test')
    print("  ✓ 已删除'小米'\n")

    # 6. 导出
    print("6. 导出词库")
    exported = km.export_to_dict(entity_id='test')
    for tag, words in exported.items():
        if words:
            print(f"  {TAGS[tag]}: {words}")

    print("\n=== 测试完成 ===")
    print("OK keywords: CRUD/查询/推荐/审核 全通")
