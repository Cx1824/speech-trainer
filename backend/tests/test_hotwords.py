"""热词提取单测。"""

from __future__ import annotations

from app.modules.asr_hotwords import extract_hotwords


class TestExtractHotwords:
    def test_resume_skills_and_projects(self):
        resume = {
            "skills": ["React", "TypeScript", "Kubernetes"],
            "projects": [{"name": "数据中台"}, {"name": "营销系统"}],
            "work": [{"company": "字节跳动", "title": "前端工程师"}],
        }
        words = extract_hotwords(resume=resume, position="前端专家", company="腾讯")
        assert "React" in words
        assert "数据中台" in words
        assert "字节跳动" in words
        assert "前端专家" in words
        assert "腾讯" in words

    def test_material_english_terms(self):
        words = extract_hotwords(material_text="Q2 DAU 从 10 万涨到 25 万，OKR 对齐后 ROI 提升明显")
        assert "DAU" in words
        assert "OKR" in words
        assert "ROI" in words

    def test_material_chinese_freq(self):
        text = "智能座舱 智能座舱 智能座舱 自动驾驶 数据闭环"
        words = extract_hotwords(material_text=text)
        assert "智能座舱" in words

    def test_empty_context(self):
        assert extract_hotwords() == []

    def test_dedup_and_limit(self):
        words = extract_hotwords(
            material_text=" ".join([f"词{i}" for i in range(600)]),
            position="岗位",
        )
        assert len(words) <= 500
        assert len(words) == len(set(words))

    def test_word_max_len(self):
        # 超长词被跳过（英文正则限 16 字符）
        words = extract_hotwords(material_text="A" * 30)
        assert "A" * 30 not in words

    def test_position_unspecified_excluded(self):
        words = extract_hotwords(position="未指定")
        assert "未指定" not in words
