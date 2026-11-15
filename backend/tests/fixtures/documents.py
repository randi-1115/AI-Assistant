"""生成评测用的真实文档（DOCX/Markdown/TXT），不提交二进制。"""

from __future__ import annotations

from pathlib import Path


def make_generic_docx(directory: Path) -> Path:
    from docx import Document

    path = directory / "generic.docx"
    document = Document()
    document.add_heading("第五章 神经网络", level=1)
    document.add_paragraph("神经网络由输入层、隐藏层和输出层组成。")
    document.add_heading("5.1 反向传播", level=2)
    document.add_paragraph(
        "反向传播通过链式法则计算损失对权重和偏置的梯度，再用梯度下降更新参数。"
    )
    document.add_paragraph("标准 BP 算法每处理一个样本就更新一次参数；累积 BP 算法累积一批样本后再更新。")
    document.save(path)
    return path


def make_heading_only_docx(directory: Path) -> Path:
    from docx import Document

    path = directory / "heading_only.docx"
    document = Document()
    document.add_heading("第五章 神经网络", level=1)
    document.add_heading("5.1 反向传播", level=2)
    document.add_heading("5.2 梯度下降", level=2)
    document.save(path)
    return path


def make_table_docx(directory: Path) -> Path:
    from docx import Document

    path = directory / "table.docx"
    document = Document()
    document.add_heading("实验设置", level=1)
    document.add_paragraph("下面是训练使用的超参数。")
    table = document.add_table(rows=3, cols=2)
    table.cell(0, 0).text = "名称"
    table.cell(0, 1).text = "值"
    table.cell(1, 0).text = "学习率"
    table.cell(1, 1).text = "0.01"
    table.cell(2, 0).text = "批大小"
    table.cell(2, 1).text = "32"
    document.save(path)
    return path


def make_text(directory: Path) -> Path:
    path = directory / "notes.txt"
    path.write_text(
        "项目代号是蓝鲸。\n\n部署窗口安排在周五晚上。\n\n回滚方案：保留上一版本镜像并切换流量。",
        encoding="utf-8",
    )
    return path


def generate_documents(directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    return {
        "generic": make_generic_docx(directory),
        "heading": make_heading_only_docx(directory),
        "table": make_table_docx(directory),
        "text": make_text(directory),
    }
