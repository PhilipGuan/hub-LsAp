# -*- coding: utf-8 -*-
"""生成一份含中文、表格、图表的测试 PDF，用于验证 MinerU 解析与多模态检索。

用法:
    python scripts/make_sample_pdf.py

输出:
    samples/2025年智能硬件市场分析报告.pdf
    samples/assets/*.png
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "samples"
ASSET_DIR = OUT_DIR / "assets"
PDF_PATH = OUT_DIR / "2025年智能硬件市场分析报告.pdf"

FONT_FILE = Path("C:/Windows/Fonts/simhei.ttf")

QUARTERS = ["Q1", "Q2", "Q3", "Q4"]
PRODUCT_A = [820, 960, 640, 410]
PRODUCT_B = [580, 640, 700, 760]


# ---------------------------------------------------------------- 图表
def make_charts():
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    fp = FontProperties(fname=str(FONT_FILE))

    # 1. 分季度销售额柱状图
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=200)
    width = 0.35
    xs = range(len(QUARTERS))
    bars_a = ax.bar([x - width / 2 for x in xs], PRODUCT_A, width, label="产品A")
    bars_b = ax.bar([x + width / 2 for x in xs], PRODUCT_B, width, label="产品B")
    ax.set_title("2025 年分季度销售额（万元）", fontproperties=fp, fontsize=16)
    ax.set_xticks(list(xs))
    ax.set_xticklabels(QUARTERS, fontproperties=fp, fontsize=12)
    ax.set_ylabel("销售额（万元）", fontproperties=fp, fontsize=12)
    ax.set_ylim(0, 1100)
    ax.legend(prop=fp, fontsize=11)
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    for bars in (bars_a, bars_b):
        for bar in bars:
            ax.annotate(
                f"{bar.get_height():.0f}",
                xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center",
                fontproperties=fp,
                fontsize=10,
            )
    fig.tight_layout()
    sales_png = ASSET_DIR / "sales_chart.png"
    fig.savefig(sales_png)
    plt.close(fig)

    # 2. 边缘设备架构示意图
    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=200)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 5)
    ax.axis("off")

    def box(x, y, w, h, text, color):
        ax.add_patch(
            plt.Rectangle((x, y), w, h, facecolor=color, edgecolor="#37474F", linewidth=1.5)
        )
        ax.text(
            x + w / 2,
            y + h / 2,
            text,
            ha="center",
            va="center",
            fontproperties=fp,
            fontsize=12,
        )

    box(0.4, 1.6, 2.4, 1.8, "X100 智能网关\n（左侧设备）", "#BBDEFB")
    box(3.8, 1.6, 2.4, 1.8, "云管理平台\n（数据存储与分析）", "#C8E6C9")
    box(7.2, 1.6, 2.4, 1.8, "移动端 App\n（远程监控）", "#FFE0B2")

    ax.annotate(
        "",
        xy=(3.75, 2.5),
        xytext=(2.85, 2.5),
        arrowprops=dict(arrowstyle="->", lw=2, color="#37474F"),
    )
    ax.annotate(
        "",
        xy=(7.15, 2.5),
        xytext=(6.25, 2.5),
        arrowprops=dict(arrowstyle="->", lw=2, color="#37474F"),
    )
    ax.text(
        3.3,
        2.75,
        "MQTT\n30 秒/次",
        ha="center",
        va="bottom",
        fontproperties=fp,
        fontsize=10,
    )
    ax.text(
        6.7,
        2.75,
        "HTTPS",
        ha="center",
        va="bottom",
        fontproperties=fp,
        fontsize=10,
    )
    ax.text(
        5.0,
        0.6,
        "图 2  边缘设备数据链路示意图",
        ha="center",
        fontproperties=fp,
        fontsize=12,
    )
    fig.tight_layout()
    diagram_png = ASSET_DIR / "device_diagram.png"
    fig.savefig(diagram_png)
    plt.close(fig)

    return sales_png, diagram_png


# ---------------------------------------------------------------- PDF
def build_pdf(sales_png, diagram_png):
    pdfmetrics.registerFont(TTFont("SimHei", str(FONT_FILE)))

    title = ParagraphStyle(
        "title", fontName="SimHei", fontSize=22, leading=30, alignment=TA_CENTER
    )
    subtitle = ParagraphStyle(
        "subtitle",
        fontName="SimHei",
        fontSize=12,
        leading=20,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#546E7A"),
    )
    h1 = ParagraphStyle(
        "h1", fontName="SimHei", fontSize=15, leading=24, spaceBefore=10, spaceAfter=8
    )
    body = ParagraphStyle(
        "body", fontName="SimHei", fontSize=10.5, leading=19, spaceAfter=6
    )
    caption = ParagraphStyle(
        "caption",
        fontName="SimHei",
        fontSize=9,
        leading=14,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#607D8B"),
    )

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("SimHei", 8)
        canvas.setFillColor(colors.HexColor("#90A4AE"))
        canvas.drawCentredString(A4[0] / 2, 1.2 * cm, f"第 {doc.page} 页")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(PDF_PATH),
        pagesize=A4,
        leftMargin=2.2 * cm,
        rightMargin=2.2 * cm,
        topMargin=2.0 * cm,
        bottomMargin=2.0 * cm,
        title="2025 年智能硬件市场分析报告",
        author="RAG 测试样例",
    )

    story = []
    story.append(Spacer(1, 2.2 * cm))
    story.append(Paragraph("2025 年智能硬件市场分析报告", title))
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph("产品线销售趋势与边缘设备部署情况", subtitle))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph("编制日期：2026 年 3 月", subtitle))
    story.append(Spacer(1, 1.6 * cm))

    story.append(Paragraph("摘要", h1))
    story.append(
        Paragraph(
            "本报告汇总 2025 年四个季度的销售与部署数据。全年实现营收 7,880 万元，"
            "同比增长 18.6%。产品A 仍是营收主力，但增长动能明显减弱；产品B 保持稳定增长。"
            "边缘侧累计部署 X100 智能网关 1,860 台，E200 边缘节点 128 个。",
            body,
        )
    )

    story.append(Paragraph("关键结论", h1))
    for text in [
        "1. 产品A 的销售额在 2025 年第三季度开始下降，Q3 环比下降 33.3%，Q4 继续降至 410 万元。",
        "2. 产品B 全年保持增长，Q4 销售额达到 760 万元，首次超过产品A。",
        "3. 华东区域贡献 42% 的营收，是最大的区域市场。",
        "4. X100 网关的默认采样间隔为 30 秒，支持 Modbus 与 RS485 两种工业接口。",
    ]:
        story.append(Paragraph(text, body))

    story.append(PageBreak())

    story.append(Paragraph("1  分季度销售表现", h1))
    story.append(
        Paragraph(
            "2025 年分季度数据如下表所示。产品A 从第三季度开始进入下行通道，"
            "主要原因是供应链切换导致交付延迟，同时竞品在 Q3 发起降价。"
            "产品B 则受益于渠道扩张，连续四个季度环比增长。",
            body,
        )
    )

    table_data = [["季度", "产品A（万元）", "产品B（万元）", "合计（万元）"]]
    for i, q in enumerate(QUARTERS):
        table_data.append(
            [q, f"{PRODUCT_A[i]}", f"{PRODUCT_B[i]}", f"{PRODUCT_A[i] + PRODUCT_B[i]}"]
        )
    table_data.append(["全年", "2,830", "2,680", "7,880"])

    table = Table(table_data, colWidths=[3.2 * cm, 4.0 * cm, 4.0 * cm, 4.0 * cm])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "SimHei"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ECEFF1")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#263238")),
                ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#B0BEC5")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#FAFAFA")]),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 0.5 * cm))

    story.append(Image(str(sales_png), width=16 * cm, height=9 * cm))
    story.append(Paragraph("图 1  2025 年分季度销售额对比", caption))
    story.append(Spacer(1, 0.3 * cm))
    story.append(
        Paragraph(
            "从图中可以看出，产品A 的柱状高度在 Q3 明显回落，而产品B 逐季上升，"
            "并在 Q4 反超产品A。",
            body,
        )
    )

    story.append(PageBreak())

    story.append(Paragraph("2  边缘设备与系统架构", h1))
    story.append(
        Paragraph(
            "图 2 展示了边缘设备的数据链路。图中左侧设备为 X100 智能网关，"
            "内置 Modbus 与 RS485 接口，默认采样间隔 30 秒，负责采集温度、湿度与电流数据，"
            "并通过 MQTT 协议上传至云管理平台；中间为云管理平台，负责数据存储与分析；"
            "右侧为移动端 App，用于远程监控与告警推送。",
            body,
        )
    )
    story.append(Spacer(1, 0.2 * cm))
    story.append(Image(str(diagram_png), width=16.5 * cm, height=7.7 * cm))
    story.append(Paragraph("图 2  边缘设备数据链路示意图", caption))

    story.append(Paragraph("2.1  设备规格", h1))
    spec = [
        ["设备型号", "核心功能", "接口", "部署数量"],
        ["X100 网关", "温湿度与电流采集", "Modbus / RS485", "1,860 台"],
        ["E200 边缘节点", "本地推理与缓存", "千兆网口", "128 个"],
    ]
    spec_table = Table(spec, colWidths=[3.6 * cm, 5.6 * cm, 3.6 * cm, 3.4 * cm])
    spec_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "SimHei"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ECEFF1")),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#B0BEC5")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(spec_table)

    story.append(PageBreak())

    story.append(Paragraph("3  结论与建议", h1))
    for text in [
        "1. 针对产品A 的下滑，建议在 2026 年 Q1 完成供应链切换，并推出面向中小客户的轻量版本。",
        "2. 产品B 已进入规模增长阶段，建议加大华东与华南渠道投入。",
        "3. 边缘侧应优先升级 X100 网关固件，将采样间隔从 30 秒缩短至 10 秒，以支撑预测性维护场景。",
        "4. 建议将 E200 节点从 128 个扩容至 200 个，缓解本地推理压力。",
    ]:
        story.append(Paragraph(text, body))

    story.append(Paragraph("数据说明", h1))
    story.append(
        Paragraph(
            "本报告数据来源于内部销售系统与设备管理平台，统计口径为含税收入，"
            "单位为万元。报告中的图表由测试脚本自动生成，仅用于检索与问答效果验证。",
            body,
        )
    )
    story.append(Spacer(1, 0.6 * cm))
    story.append(Paragraph("— 报告完 —", caption))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sales_png, diagram_png = make_charts()
    build_pdf(sales_png, diagram_png)
    print(f"已生成: {PDF_PATH}")
    print(f"图表: {sales_png.name}, {diagram_png.name}")


if __name__ == "__main__":
    main()
