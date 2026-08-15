"""面试会话数据模型。"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.core.database import Base


class InterviewSessionRow(Base):
    """面试会话。"""

    __tablename__ = "interview_session"

    id = Column(String(36), primary_key=True)  # uuid
    scenario = Column(String(32), nullable=False, default="interview")  # 场景：interview/presentation/speech
    position = Column(String(64), nullable=False)
    level = Column(String(32), nullable=False, default="中级")
    style = Column(String(32), nullable=False, default="professional")  # 面试官风格
    company = Column(String(128), default="")           # 公司名称
    jd_url = Column(String(512), default="")            # JD 链接
    jd_content = Column(Text, default="")               # JD 内容（手动粘贴或抓取结果）
    resume_file = Column(String(256), default="")
    resume_parsed_json = Column(Text, default="")
    material_file = Column(String(256), default="")     # 汇报/演讲材料文件名
    material_text = Column(Text, default="")            # 材料解析文本
    duration_limit = Column(Integer, default=0)         # 时长上限（分钟），0=不限
    started_at = Column(DateTime)                       # 正式开始回答时间（计时基准）
    status = Column(String(32), default="configuring")  # configuring/in_progress/completed/aborted
    current_stage = Column(String(32), default="")  # opening/self_intro/project/position/qa/report
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    dialogues = relationship(
        "InterviewDialogueRow",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="InterviewDialogueRow.seq",
    )


class InterviewDialogueRow(Base):
    """面试对话片段。"""

    __tablename__ = "interview_dialogue"

    id = Column(String(36), primary_key=True)
    session_id = Column(String(36), ForeignKey("interview_session.id"), nullable=False)
    seq = Column(Integer, nullable=False)  # 自增序号
    role = Column(String(8), nullable=False)  # ai / user
    stage = Column(String(32), default="")
    text = Column(Text, default="")
    audio_url = Column(String(256), default="")
    analysis_json = Column(Text, default="")  # user 消息的实时分析结果
    created_at = Column(DateTime, server_default=func.now())

    session = relationship("InterviewSessionRow", back_populates="dialogues")
