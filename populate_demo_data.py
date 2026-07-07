# -*- coding: utf-8 -*-
"""填充监控页面演示数据"""
from yuqing.store import Store, CleanDoc
from yuqing import embed

# 删除旧数据库，创建新的
import os
if os.path.exists('yuqing.db'):
    os.remove('yuqing.db')

s = Store('yuqing.db')

print("构造丰富的模拟数据...")

# 模拟数据：涵盖多个平台、真实链接格式
test_posts = [
    # 微博 - 负面
    {'platform': 'weibo', 'id': 'O12345678', 'author': '数码博主王小明', 'followers': 52000,
     'text': '星海手机发热严重，玩游戏20分钟就烫手，客服态度恶劣不解决问题！',
     'likes': 1520, 'pol': 'neg', 'url': 'https://weibo.com/1234567890/O12345678'},

    {'platform': 'weibo', 'id': 'O23456789', 'author': '消费者李华', 'followers': 1200,
     'text': '刚买的星海Pro电池续航太差，一天三充还不够用，严重影响使用体验',
     'likes': 890, 'pol': 'neg', 'url': 'https://weibo.com/9876543210/O23456789'},

    {'platform': 'weibo', 'id': 'O34567890', 'author': '科技评测张三', 'followers': 85000,
     'text': '星海手机系统卡顿频繁死机，售后推诿不给换货，大家避雷！',
     'likes': 2340, 'pol': 'neg', 'url': 'https://weibo.com/tech_zhang/O34567890'},

    # 微博 - 正面
    {'platform': 'weibo', 'id': 'P12345678', 'author': '手机测评师', 'followers': 32000,
     'text': '星海手机屏幕显示效果真不错，色彩鲜艳细腻，拍照效果也很赞',
     'likes': 560, 'pol': 'pos', 'url': 'https://weibo.com/reviewer01/P12345678'},

    {'platform': 'weibo', 'id': 'P23456789', 'author': '数码爱好者', 'followers': 5600,
     'text': '入手星海Pro一周，性价比真的高，推荐给预算有限的朋友',
     'likes': 320, 'pol': 'pos', 'url': 'https://weibo.com/digi_lover/P23456789'},

    # 小红书 - 负面
    {'platform': 'xiaohongshu', 'id': 'abc123456', 'author': '小红薯用户A', 'followers': 2300,
     'text': '星海手机充电太慢了，而且发热明显，不建议购买',
     'likes': 234, 'pol': 'neg', 'url': 'https://www.xiaohongshu.com/explore/abc123456'},

    {'platform': 'xiaohongshu', 'id': 'def234567', 'author': '小红薯用户B', 'followers': 890,
     'text': '用了两个月就出现屏幕闪烁，联系售后说过保不给修，太坑了',
     'likes': 156, 'pol': 'neg', 'url': 'https://www.xiaohongshu.com/explore/def234567'},

    # 小红书 - 正面
    {'platform': 'xiaohongshu', 'id': 'xyz789012', 'author': '小红薯测评师', 'followers': 12000,
     'text': '星海手机外观设计很时尚，手感不错，适合年轻人使用',
     'likes': 445, 'pol': 'pos', 'url': 'https://www.xiaohongshu.com/explore/xyz789012'},

    # 知乎 - 负面
    {'platform': 'zhihu', 'id': '456789012', 'author': '知乎用户甲', 'followers': 15000,
     'text': '如何评价星海手机？个人体验很差，系统优化不足，应用经常闪退',
     'likes': 789, 'pol': 'neg', 'url': 'https://www.zhihu.com/question/456789012/answer/987654321'},

    {'platform': 'zhihu', 'id': '567890123', 'author': '知乎用户乙', 'followers': 8900,
     'text': '星海Pro续航测试：重度使用半天就没电，不如竞品',
     'likes': 432, 'pol': 'neg', 'url': 'https://www.zhihu.com/question/567890123/answer/876543210'},

    # 知乎 - 正面
    {'platform': 'zhihu', 'id': '678901234', 'author': '知乎数码达人', 'followers': 23000,
     'text': '星海手机在千元机中算是不错的选择，基本功能都能满足',
     'likes': 234, 'pol': 'pos', 'url': 'https://www.zhihu.com/question/678901234/answer/765432109'},

    # 黑猫投诉 - 负面
    {'platform': 'heimao', 'id': 'complaint001', 'author': '投诉用户001', 'followers': 0,
     'text': '星海手机购买后三天出现质量问题，商家拒绝退换货，要求赔偿',
     'likes': 0, 'pol': 'neg', 'is_complaint': True,
     'url': 'https://tousu.sina.com.cn/complaint/view/17123456789/'},

    {'platform': 'heimao', 'id': 'complaint002', 'author': '投诉用户002', 'followers': 0,
     'text': '星海官方旗舰店虚假宣传，实际配置与描述不符，申请全额退款',
     'likes': 0, 'pol': 'neg', 'is_complaint': True,
     'url': 'https://tousu.sina.com.cn/complaint/view/17234567890/'},

    # 抖音 - 正面
    {'platform': 'douyin', 'id': 'dy123456', 'author': '抖音测评号', 'followers': 156000,
     'text': '星海手机开箱测评，外观漂亮，系统流畅，值得入手',
     'likes': 12300, 'pol': 'pos', 'url': 'https://www.douyin.com/video/dy123456'},

    # 抖音 - 负面
    {'platform': 'douyin', 'id': 'dy234567', 'author': '抖音用户吐槽', 'followers': 3400,
     'text': '星海手机信号差，地铁里经常断网，不推荐',
     'likes': 5600, 'pol': 'neg', 'url': 'https://www.douyin.com/video/dy234567'},
]

entity_id = '星海手机'
now = '2026-07-07T18:00:00+08:00'

for i, post in enumerate(test_posts):
    d = CleanDoc.build(
        platform=post['platform'],
        entity_id=entity_id,
        native_id=post['id'],
        text=post['text'],
        author=post.get('author', '匿名用户'),
        author_followers=post.get('followers', 0),
        likes=post.get('likes', 0),
        publish_ts=f'2026-07-0{(i % 5) + 1}T{10 + (i % 12)}:00:00',
        fetched_at=now,
        url=post.get('url'),
        is_complaint=post.get('is_complaint', False)
    )
    s.add_clean(d)

    # 添加特征
    aspects = []
    if '发热' in post['text'] or '烫' in post['text']:
        aspects.append({'aspect': '性能', 'polarity': 'neg' if post['pol'] == 'neg' else 'pos'})
    if '续航' in post['text'] or '电池' in post['text']:
        aspects.append({'aspect': '续航', 'polarity': 'neg' if post['pol'] == 'neg' else 'pos'})
    if '售后' in post['text'] or '客服' in post['text']:
        aspects.append({'aspect': '服务', 'polarity': 'neg' if post['pol'] == 'neg' else 'pos'})
    if '屏幕' in post['text'] or '外观' in post['text']:
        aspects.append({'aspect': '外观', 'polarity': 'neg' if post['pol'] == 'neg' else 'pos'})

    signals = {'aspects': aspects} if aspects else {}
    if post.get('is_complaint'):
        signals['crisis'] = True

    s.add_feature(d.doc_id, {
        'polarity': post['pol'],
        'risk': 5 if post.get('is_complaint') else 3 if post['pol'] == 'neg' else 0,
        'signals': signals,
        'topic_label': aspects[0]['aspect'] if aspects else '其他'
    })

s.commit()

print(f"已添加 {len(test_posts)} 条数据")
print("正在向量化...")
n_vec = embed.ensure_embeddings(s, now=now)
print(f"已向量化 {n_vec} 条")

# 统计
stats = s.conn.execute('''
    SELECT
        COUNT(DISTINCT platform) as platforms,
        COUNT(*) as total,
        SUM(CASE WHEN f.polarity='neg' THEN 1 ELSE 0 END) as negative,
        SUM(CASE WHEN f.polarity='pos' THEN 1 ELSE 0 END) as positive
    FROM clean c JOIN features f USING(doc_id)
''').fetchone()

s.close()

print(f"\n✓ 数据统计:")
print(f"  覆盖平台: {stats[0]} 个 (微博/小红书/知乎/黑猫/抖音)")
print(f"  总帖子数: {stats[1]} 条")
print(f"  负面帖子: {stats[2]} 条 (含2条投诉)")
print(f"  正面帖子: {stats[3]} 条")
print(f"  已向量化: {n_vec} 条")
print(f"\n✓ 监控页面展示数据已完善")
