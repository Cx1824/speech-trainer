"""面试会话 Schema。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ResumeStructured(BaseModel):
    basics: dict = Field(default_factory=dict)
    education: list[dict] = Field(default_factory=list)
    work: list[dict] = Field(default_factory=list)
    projects: list[dict] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    position_guess: str = ""
    level_guess: str = ""


class InterviewConfigIn(BaseModel):
    """创建面试会话。"""

    position: str = Field(default="", description="岗位，可由简历解析后填充")
    level: str = Field(default="中级", description="职级")
    style: str = Field(default="professional", description="面试官风格")
    company: str = Field(default="", description="公司名称")
    jd_url: str = Field(default="", description="JD 链接")
    jd_content: str = Field(default="", description="JD 内容（手动粘贴或抓取结果）")


class InterviewSessionOut(BaseModel):
    id: str
    position: str
    level: str
    style: str
    company: str = ""
    jd_url: str = ""
    jd_content: str = ""
    status: str
    current_stage: str
    has_resume: bool = False
    resume_parsed: ResumeStructured | None = None


class FetchJDIn(BaseModel):
    """抓取 JD 请求。"""

    url: str = Field(..., description="JD 网页链接")


class FetchJDOut(BaseModel):
    title: str = ""
    company: str = ""
    content: str = ""
    url: str = ""
    success: bool = True
    message: str = ""


class DialogueOut(BaseModel):
    id: str
    seq: int
    role: str
    stage: str
    text: str
    audio_url: str = ""


class NextQuestionOut(BaseModel):
    """LLM 生成的下一个问题。"""

    stage: str
    text: str
    is_followup: bool = False  # 是否追问
