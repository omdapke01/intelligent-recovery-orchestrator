#!/usr/bin/env python3
"""
generate_pitch_deck.py
======================
Generates the official IRO (Intelligent Recovery Orchestrator) pitch presentation
deck for the Razorpay AI Buildathon 2026.

Positioning: "IRO — The Autonomous AI Teammate for Payment Operations"
Track: Track 3 — Autonomous AI Teammates
Format: 16:9 Widescreen (13.333 x 7.5 inches)
Theme: Dark Fintech Operations Center (Razorpay Blue, Deep Navy, Cyan, White)
Notes: Full presenter pitch script embedded into PowerPoint Speaker Notes.
"""

import os
import sys

# Ensure UTF-8 stdout encoding on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

# ==============================================================================
# COLOR PALETTE (Razorpay Corporate & Dark Fintech Operations Identity)
# ==============================================================================
BG_DARK        = RGBColor(0x07, 0x0D, 0x1E)  # Deepest Navy background (#070d1e)
CARD_BG        = RGBColor(0x0E, 0x17, 0x2E)  # Standard card container (#0e172e)
CARD_BG_ALT    = RGBColor(0x13, 0x22, 0x47)  # Elevated accent card (#132247)
CARD_BG_DARK   = RGBColor(0x0A, 0x11, 0x24)  # Inset card (#0a1124)
TEXT_WHITE     = RGBColor(0xFF, 0xFF, 0xFF)  # Pure white (#ffffff)
TEXT_DIM       = RGBColor(0x94, 0xA3, 0xB8)  # Slate dim (#94a3b8)
TEXT_MUTED     = RGBColor(0x64, 0x74, 0x8B)  # Slate muted (#64748b)
BLUE_BRAND     = RGBColor(0x02, 0x84, 0xC7)  # Razorpay Signature Brand Blue
BLUE_VIBRANT   = RGBColor(0x25, 0x63, 0xEB)  # Electric Blue accent (#2563eb)
BLUE_LIGHT     = RGBColor(0x60, 0xA5, 0xFA)  # Soft Blue for titles (#60a5fa)
CYAN_ACCENT    = RGBColor(0x06, 0xB6, 0xD4)  # High-tech Cyan/Teal (#06b6d4)
EMERALD_GREEN  = RGBColor(0x10, 0xB9, 0x81)  # Recovery Emerald (#10b981)
AMBER_WARN     = RGBColor(0xF5, 0x9E, 0x0B)  # Warning Amber (#f59e0b)
RED_DANGER     = RGBColor(0xEF, 0x44, 0x44)  # Payment Failure Red (#ef4444)
BORDER_SUBTLE  = RGBColor(0x1E, 0x29, 0x3B)  # Card border subtle (#1e293b)
BORDER_BLUE    = RGBColor(0x3B, 0x82, 0xF6)  # Active card border blue (#3b82f6)

FONT_MAIN = "Segoe UI"
FONT_CODE = "Consolas"

# ==============================================================================
# HELPER UTILITIES FOR SHAPES, TEXT & CARDS
# ==============================================================================
def create_blank_slide(prs):
    """Creates a blank slide with the default deep navy background."""
    blank_layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank_layout)
    bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height)
    bg.fill.solid()
    bg.fill.fore_color.rgb = BG_DARK
    bg.line.fill.background()
    return slide

def add_header(slide, category, title, subtitle=None):
    """Adds a standardized top header banner with category badge, title and subtitle."""
    # Top Category Badge
    badge_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.35))
    tf = badge_box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.text = category.upper()
    p.font.name = FONT_MAIN
    p.font.size = Pt(10)
    p.font.bold = True
    p.font.color.rgb = CYAN_ACCENT

    # Main Slide Title
    title_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.72), Inches(11.7), Inches(0.55))
    tf_t = title_box.text_frame
    tf_t.word_wrap = True
    tf_t.margin_left = tf_t.margin_top = tf_t.margin_right = tf_t.margin_bottom = 0
    p_t = tf_t.paragraphs[0]
    p_t.text = title
    p_t.font.name = FONT_MAIN
    p_t.font.size = Pt(22)
    p_t.font.bold = True
    p_t.font.color.rgb = TEXT_WHITE

    # Optional Subtitle
    if subtitle:
        sub_box = slide.shapes.add_textbox(Inches(0.8), Inches(1.3), Inches(11.7), Inches(0.35))
        tf_s = sub_box.text_frame
        tf_s.word_wrap = True
        tf_s.margin_left = tf_s.margin_top = tf_s.margin_right = tf_s.margin_bottom = 0
        p_s = tf_s.paragraphs[0]
        p_s.text = subtitle
        p_s.font.name = FONT_MAIN
        p_s.font.size = Pt(12)
        p_s.font.color.rgb = TEXT_DIM

def add_card(slide, left, top, width, height, bg_color=CARD_BG, border_color=BORDER_SUBTLE):
    """Adds a stylized card container with solid fill and border."""
    card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
    card.fill.solid()
    card.fill.fore_color.rgb = bg_color
    card.line.color.rgb = border_color
    card.line.width = Pt(1)
    return card

def add_speaker_notes(slide, notes_text):
    """Sets the presenter speaker notes text for the slide."""
    notes_slide = slide.notes_slide
    text_frame = notes_slide.notes_text_frame
    text_frame.text = notes_text

# ==============================================================================
# SLIDE 1: TITLE — AUTONOMOUS AI TEAMMATE FOR PAYMENT OPERATIONS
# ==============================================================================
def build_slide_1(prs):
    slide = create_blank_slide(prs)

    # Top Brand Pill
    pill = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.8), Inches(0.9), Inches(5.6), Inches(0.42))
    pill.fill.solid()
    pill.fill.fore_color.rgb = CARD_BG_ALT
    pill.line.color.rgb = BLUE_VIBRANT
    tf_pill = pill.text_frame
    tf_pill.margin_left = Inches(0.18)
    p_pill = tf_pill.paragraphs[0]
    p_pill.text = "RAZORPAY AI BUILDATHON 2026 • TRACK 3: AUTONOMOUS AI TEAMMATES"
    p_pill.font.name = FONT_MAIN
    p_pill.font.size = Pt(9.5)
    p_pill.font.bold = True
    p_pill.font.color.rgb = CYAN_ACCENT

    # Main Project Title
    title_box = slide.shapes.add_textbox(Inches(0.8), Inches(1.45), Inches(11.7), Inches(1.1))
    tf = title_box.text_frame
    p = tf.paragraphs[0]
    p.text = "IRO"
    p.font.name = FONT_MAIN
    p.font.size = Pt(44)
    p.font.bold = True
    p.font.color.rgb = TEXT_WHITE

    p_sub = tf.add_paragraph()
    p_sub.text = "The Autonomous AI Teammate for Payment Operations"
    p_sub.font.name = FONT_MAIN
    p_sub.font.size = Pt(24)
    p_sub.font.bold = True
    p_sub.font.color.rgb = BLUE_LIGHT

    # Subtitle / Core Motto
    motto_box = slide.shapes.add_textbox(Inches(0.8), Inches(2.75), Inches(11.7), Inches(0.45))
    tf_motto = motto_box.text_frame
    p_motto = tf_motto.paragraphs[0]
    p_motto.text = "Investigate.  Reason.  Orchestrate.  Verify."
    p_motto.font.name = FONT_CODE
    p_motto.font.size = Pt(14)
    p_motto.font.bold = True
    p_motto.font.color.rgb = CYAN_ACCENT

    # Teammate Positioning Box
    pos_card = add_card(slide, Inches(0.8), Inches(3.35), Inches(11.7), Inches(1.2), bg_color=CARD_BG_ALT, border_color=BORDER_BLUE)
    tb_pos = slide.shapes.add_textbox(Inches(1.1), Inches(3.45), Inches(11.1), Inches(1.0))
    tf_pos = tb_pos.text_frame
    p_pos1 = tf_pos.paragraphs[0]
    p_pos1.text = "CORE POSITIONING"
    p_pos1.font.name = FONT_MAIN
    p_pos1.font.size = Pt(10)
    p_pos1.font.bold = True
    p_pos1.font.color.rgb = BLUE_LIGHT

    p_pos2 = tf_pos.add_paragraph()
    p_pos2.text = "“When a payment fails, IRO doesn't just detect the failure. It investigates what happened, reasons about the context, recommends the appropriate recovery path, safely orchestrates the action, and verifies the outcome.”"
    p_pos2.font.name = FONT_MAIN
    p_pos2.font.size = Pt(12)
    p_pos2.font.italic = True
    p_pos2.font.color.rgb = TEXT_WHITE

    # 4 Quick Metric Badges
    stats = [
        ("57.0%", "RECOVERY RATE", "vs 32.0% Baseline (+78% lift)", EMERALD_GREEN),
        ("₹231,069", "REVENUE RECOVERED", "+₹83,412 net gain", BLUE_LIGHT),
        ("0 UNSAFE", "ACTIONS EXECUTED", "24 attempts blocked safely", CYAN_ACCENT),
        ("2,920x", "AI COST ROI", "₹28.56 total inference cost", AMBER_WARN)
    ]

    card_w = Inches(2.7)
    card_h = Inches(1.3)
    start_x = Inches(0.8)
    gap_x = Inches(0.3)
    y_pos = Inches(4.75)

    for i, (val, label, subtext, color) in enumerate(stats):
        cx = start_x + i * (card_w + gap_x)
        add_card(slide, cx, y_pos, card_w, card_h, bg_color=CARD_BG, border_color=BORDER_SUBTLE)
        
        vbox = slide.shapes.add_textbox(cx + Inches(0.18), y_pos + Inches(0.12), card_w - Inches(0.36), Inches(0.45))
        tf_v = vbox.text_frame
        p_v = tf_v.paragraphs[0]
        p_v.text = val
        p_v.font.name = FONT_MAIN
        p_v.font.size = Pt(20)
        p_v.font.bold = True
        p_v.font.color.rgb = color

        lbox = slide.shapes.add_textbox(cx + Inches(0.18), y_pos + Inches(0.58), card_w - Inches(0.36), Inches(0.3))
        tf_l = lbox.text_frame
        p_l = tf_l.paragraphs[0]
        p_l.text = label
        p_l.font.name = FONT_MAIN
        p_l.font.size = Pt(9.5)
        p_l.font.bold = True
        p_l.font.color.rgb = TEXT_WHITE

        sbox = slide.shapes.add_textbox(cx + Inches(0.18), y_pos + Inches(0.88), card_w - Inches(0.36), Inches(0.3))
        tf_s = sbox.text_frame
        p_s = tf_s.paragraphs[0]
        p_s.text = subtext
        p_s.font.name = FONT_MAIN
        p_s.font.size = Pt(8.5)
        p_s.font.color.rgb = TEXT_MUTED

    # Bottom Credits Bar
    meta_box = slide.shapes.add_textbox(Inches(0.8), Inches(6.3), Inches(11.7), Inches(0.4))
    tf_m = meta_box.text_frame
    p_m = tf_m.paragraphs[0]
    p_m.text = "Built by Om Dapke  •  github.com/omdapke01/intelligent-recovery-orchestrator  •  120 / 120 Pytest Passing"
    p_m.font.name = FONT_MAIN
    p_m.font.size = Pt(10.5)
    p_m.font.color.rgb = TEXT_MUTED

    add_speaker_notes(slide, 
        "Welcome everyone. We are presenting IRO — the Autonomous AI Teammate for Payment Operations.\n\n"
        "When a payment fails, IRO doesn't just detect the failure. It investigates what happened, reasons about the context, recommends the appropriate recovery path, safely orchestrates the action, and verifies the outcome.\n\n"
        "Investigate. Reason. Orchestrate. Verify.\n\n"
        "Our goal is not to replace payment operations, but to give payment teams an autonomous teammate that makes them faster, safer, and vastly more profitable."
    )

# ==============================================================================
# SLIDE 2: PROBLEM STATEMENT — "A failed payment isn't necessarily a failed transaction."
# ==============================================================================
def build_slide_2(prs):
    slide = create_blank_slide(prs)
    add_header(slide, "01. Problem Statement", "A failed payment isn't necessarily a failed transaction.",
               "The hard problem in payment operations isn't detecting failure. It's deciding what to do next — safely.")

    col_w = Inches(5.7)
    col_h = Inches(4.7)
    y = Inches(1.9)

    # Left Column: Traditional Blind Automation
    add_card(slide, Inches(0.8), y, col_w, col_h, bg_color=CARD_BG, border_color=RED_DANGER)
    tb1 = slide.shapes.add_textbox(Inches(1.1), y + Inches(0.25), col_w - Inches(0.6), col_h - Inches(0.5))
    tf1 = tb1.text_frame
    p1 = tf1.paragraphs[0]
    p1.text = "TRADITIONAL AUTOMATION: BLIND RETRIES"
    p1.font.name = FONT_MAIN
    p1.font.size = Pt(13)
    p1.font.bold = True
    p1.font.color.rgb = RED_DANGER

    p1_f = tf1.add_paragraph()
    p1_f.text = "IF PAYMENT FAILS → RETRY IMMEDIATELY"
    p1_f.font.name = FONT_CODE
    p1_f.font.size = Pt(11)
    p1_f.font.bold = True
    p1_f.font.color.rgb = TEXT_WHITE

    p1_body = tf1.add_paragraph()
    p1_body.text = (
        "\n• Flawed Assumption: Treats every payment failure as a simple transient network glitch.\n\n"
        "• Catastrophic Consequences in Fintech:\n"
        "  - Double debits when timeouts are retried blindly\n"
        "  - Repetitive declines when banking routes are degraded\n"
        "  - Card block triggers from issuing bank fraud algorithms\n"
        "  - Customer drop-off and cart abandonment\n\n"
        "• Result: The wrong recovery decision is worse than no recovery at all."
    )
    p1_body.font.name = FONT_MAIN
    p1_body.font.size = Pt(10.5)
    p1_body.font.color.rgb = TEXT_DIM

    # Right Column: IRO Autonomous Operations Decision Tree
    add_card(slide, Inches(6.8), y, col_w, col_h, bg_color=CARD_BG, border_color=CYAN_ACCENT)
    tb2 = slide.shapes.add_textbox(Inches(7.1), y + Inches(0.25), col_w - Inches(0.6), col_h - Inches(0.5))
    tf2 = tb2.text_frame
    p2 = tf2.paragraphs[0]
    p2.text = "IRO: AUTONOMOUS DECISION ENGINEERING"
    p2.font.name = FONT_MAIN
    p2.font.size = Pt(13)
    p2.font.bold = True
    p2.font.color.rgb = CYAN_ACCENT

    p2_f = tf2.add_paragraph()
    p2_f.text = "FAILURE → CONTEXT → REASON → POLICY CHECK → SAFE ACTION"
    p2_f.font.name = FONT_CODE
    p2_f.font.size = Pt(10.5)
    p2_f.font.bold = True
    p2_f.font.color.rgb = TEXT_WHITE

    p2_body = tf2.add_paragraph()
    p2_body.text = (
        "\nIRO analyzes the failure and chooses among 5 distinct recovery decisions:\n\n"
        "1. Retry? — Transient blip with exponential backoff\n"
        "2. Switch route? — Bank gateway degraded; failover to healthy rail\n"
        "3. Ask customer? — 3DS OTP expired or balance issue; send recovery link\n"
        "4. Wait? — Ambiguous timeout; hold for bank reconciliation\n"
        "5. STOP? — Hard decline, fraud flag, or merchant limit reached"
    )
    p2_body.font.name = FONT_MAIN
    p2_body.font.size = Pt(10.5)
    p2_body.font.color.rgb = TEXT_DIM

    add_speaker_notes(slide,
        "A failed payment isn't necessarily a failed transaction. It's a decision problem.\n\n"
        "Do we retry? Switch the payment route? Ask the customer to take action? Wait for reconciliation? Or stop?\n\n"
        "The hard problem in payment operations isn't detecting failure. It's deciding what to do next — safely.\n\n"
        "Traditional automation blindly retries, causing double debits and bank fraud blocks. In payments, the wrong recovery decision can be far worse than no recovery at all.\n\n"
        "IRO investigates the context first, determines the right strategy, checks financial policy, and only then executes safely."
    )

# ==============================================================================
# SLIDE 3: PROPOSED SOLUTION — MEET IRO: THE AUTONOMOUS AI TEAMMATE
# ==============================================================================
def build_slide_3(prs):
    slide = create_blank_slide(prs)
    add_header(slide, "02. Proposed Solution", "Meet IRO: The Autonomous AI Teammate for Payment Operations",
               "An AI teammate that investigates ambiguous payment failures, reasons about recovery, and orchestrates safe actions.")

    # 5 Responsibilities Flow Cards
    steps = [
        ("1. INVESTIGATE", "Context Telemetry", "Gathers multi-rail telemetry, previous attempt sequence, customer profile, and merchant SLA."),
        ("2. REASON", "Strategic Evaluation", "Analyzes route health, failure codes, customer risk tier, and historical payment patterns."),
        ("3. RECOMMEND", "Structured Proposal", "Synthesizes structured, typed recovery strategy with clear audit rationale (READ-ONLY)."),
        ("4. ORCHESTRATE", "Policy Validation", "Passes through deterministic financial policy guards, distributed locks, and idempotency barrier."),
        ("5. VERIFY", "Outcome Verification", "Verifies settlement status with banking rail, updates state machine, and logs immutable audit trail."),
    ]

    card_w = Inches(2.18)
    card_h = Inches(3.6)
    gap_x = Inches(0.2)
    start_x = Inches(0.8)
    y = Inches(1.9)

    for i, (step_num, title, text) in enumerate(steps):
        cx = start_x + i * (card_w + gap_x)
        border_c = BLUE_VIBRANT if i == 2 else (EMERALD_GREEN if i == 4 else BORDER_SUBTLE)
        add_card(slide, cx, y, card_w, card_h, bg_color=CARD_BG, border_color=border_c)

        tb = slide.shapes.add_textbox(cx + Inches(0.15), y + Inches(0.2), card_w - Inches(0.3), card_h - Inches(0.4))
        tf = tb.text_frame
        tf.word_wrap = True

        p1 = tf.paragraphs[0]
        p1.text = step_num
        p1.font.name = FONT_MAIN
        p1.font.size = Pt(11)
        p1.font.bold = True
        p1.font.color.rgb = CYAN_ACCENT

        p2 = tf.add_paragraph()
        p2.text = title
        p2.font.name = FONT_MAIN
        p2.font.size = Pt(12)
        p2.font.bold = True
        p2.font.color.rgb = TEXT_WHITE

        p3 = tf.add_paragraph()
        p3.text = f"\n{text}"
        p3.font.name = FONT_MAIN
        p3.font.size = Pt(9.5)
        p3.font.color.rgb = TEXT_DIM

    # Bottom Core Safety Rule Card
    rule_card = add_card(slide, Inches(0.8), Inches(5.75), Inches(11.7), Inches(1.2), bg_color=CARD_BG_ALT, border_color=EMERALD_GREEN)
    tb_r = slide.shapes.add_textbox(Inches(1.1), Inches(5.85), Inches(11.1), Inches(1.0))
    tf_r = tb_r.text_frame
    p_r1 = tf_r.paragraphs[0]
    p_r1.text = "THE AUTONOMOUS TEAMMATE PRINCIPLE: AI PROPOSES, THE SYSTEM DECIDES"
    p_r1.font.name = FONT_MAIN
    p_r1.font.size = Pt(11)
    p_r1.font.bold = True
    p_r1.font.color.rgb = EMERALD_GREEN

    p_r2 = tf_r.add_paragraph()
    p_r2.text = "“AI has reasoning authority, not financial authority. The AI teammate can think, investigate, and recommend. But deterministic systems decide whether its recommendation is allowed.”"
    p_r2.font.name = FONT_MAIN
    p_r2.font.size = Pt(12)
    p_r2.font.bold = True
    p_r2.font.color.rgb = TEXT_WHITE

    add_speaker_notes(slide,
        "Meet IRO — an autonomous AI teammate for payment operations.\n\n"
        "IRO handles the entire lifecycle of an ambiguous payment failure across five core responsibilities: Investigate, Reason, Recommend, Orchestrate, and Verify.\n\n"
        "When an ambiguous failure arrives, the teammate gathers multi-rail telemetry, reasons through the context, and proposes the optimal recovery path.\n\n"
        "Here is our most fundamental architectural principle:\n\n"
        "AI has reasoning authority, not financial authority.\n"
        "The teammate investigates and proposes, but deterministic systems decide whether any action is permitted."
    )

# ==============================================================================
# SLIDE 4: HOW THE AI TEAMMATE WORKS (6 BOUNDED READ-ONLY TOOLS)
# ==============================================================================
def build_slide_4(prs):
    slide = create_blank_slide(prs)
    add_header(slide, "03. Agent Capabilities & Tooling", "How the AI Teammate Works: 6 Bounded Read-Only Tools",
               "The agent investigates payment context with deep precision — but has zero direct write access to move money.")

    tools = [
        ("1. Payment Context", "inspect_payment_context", "Amount, currency, merchant ID, customer ID, payment method (UPI, Card, Netbanking).", CYAN_ACCENT),
        ("2. Payment Attempts", "inspect_payment_attempts", "Sequence of prior attempts, gateway latencies, timestamps, and route history.", BLUE_LIGHT),
        ("3. Failure History", "inspect_failure_history", "Error taxonomy classification (Timeout, Socket Drop, Balance, Route Degradation).", BLUE_VIBRANT),
        ("4. Customer Profile", "inspect_customer_profile", "Customer risk tier, historical payment success rate, and chargeback signals.", AMBER_WARN),
        ("5. Merchant Policy", "inspect_merchant_policy", "Merchant-specific max retry caps, permitted recovery methods, and SLA windows.", RED_DANGER),
        ("6. Route Health", "inspect_route_health", "Real-time banking rail health scores (e.g. HDFC 38% vs ICICI 98.4% uptime).", EMERALD_GREEN),
    ]

    card_w = Inches(3.7)
    card_h = Inches(1.8)
    start_x = Inches(0.8)
    gap_x = Inches(0.3)
    y1 = Inches(1.9)
    y2 = Inches(3.9)

    for i, (title, func, desc, col) in enumerate(tools[:3]):
        cx = start_x + i * (card_w + gap_x)
        add_card(slide, cx, y1, card_w, card_h, bg_color=CARD_BG, border_color=BORDER_SUBTLE)
        tb = slide.shapes.add_textbox(cx + Inches(0.18), y1 + Inches(0.15), card_w - Inches(0.36), card_h - Inches(0.3))
        tf = tb.text_frame
        p = tf.paragraphs[0]
        p.text = title
        p.font.name = FONT_MAIN
        p.font.size = Pt(13)
        p.font.bold = True
        p.font.color.rgb = col
        p_f = tf.add_paragraph()
        p_f.text = func
        p_f.font.name = FONT_CODE
        p_f.font.size = Pt(9.5)
        p_f.font.color.rgb = TEXT_WHITE
        p_d = tf.add_paragraph()
        p_d.text = desc
        p_d.font.name = FONT_MAIN
        p_d.font.size = Pt(9.5)
        p_d.font.color.rgb = TEXT_DIM

    for i, (title, func, desc, col) in enumerate(tools[3:]):
        cx = start_x + i * (card_w + gap_x)
        add_card(slide, cx, y2, card_w, card_h, bg_color=CARD_BG, border_color=BORDER_SUBTLE)
        tb = slide.shapes.add_textbox(cx + Inches(0.18), y2 + Inches(0.15), card_w - Inches(0.36), card_h - Inches(0.3))
        tf = tb.text_frame
        p = tf.paragraphs[0]
        p.text = title
        p.font.name = FONT_MAIN
        p.font.size = Pt(13)
        p.font.bold = True
        p.font.color.rgb = col
        p_f = tf.add_paragraph()
        p_f.text = func
        p_f.font.name = FONT_CODE
        p_f.font.size = Pt(9.5)
        p_f.font.color.rgb = TEXT_WHITE
        p_d = tf.add_paragraph()
        p_d.text = desc
        p_d.font.name = FONT_MAIN
        p_d.font.size = Pt(9.5)
        p_d.font.color.rgb = TEXT_DIM

    # Bottom Safety Guarantee Banner
    sec_card = add_card(slide, Inches(0.8), Inches(5.95), Inches(11.7), Inches(1.0), bg_color=CARD_BG_ALT, border_color=CYAN_ACCENT)
    tb_sec = slide.shapes.add_textbox(Inches(1.1), Inches(6.05), Inches(11.1), Inches(0.8))
    tf_sec = tb_sec.text_frame
    p_sec1 = tf_sec.paragraphs[0]
    p_sec1.text = "HARD ISOLATION BOUNDARY: ZERO WRITE ACCESS"
    p_sec1.font.name = FONT_MAIN
    p_sec1.font.size = Pt(10.5)
    p_sec1.font.bold = True
    p_sec1.font.color.rgb = CYAN_ACCENT
    p_sec2 = tf_sec.add_paragraph()
    p_sec2.text = "The AI teammate operates in a read-only investigation sandbox. It can inspect context and output structured JSON proposals, but cannot directly invoke payment APIs or mutate database states."
    p_sec2.font.name = FONT_MAIN
    p_sec2.font.size = Pt(10)
    p_sec2.font.color.rgb = TEXT_DIM

    add_speaker_notes(slide,
        "How does the AI teammate actually work?\n\n"
        "We equip the agent with six bounded read-only tools: payment context, attempt history, failure taxonomy, customer profile, merchant policy, and real-time route health telemetry.\n\n"
        "The agent calls these tools to build a multi-dimensional picture of the failure.\n\n"
        "It then performs structured reasoning and outputs a typed recovery recommendation.\n\n"
        "Notice what is absent: there are ZERO write tools.\n\n"
        "The agent can investigate and recommend, but cannot directly execute payment actions."
    )

# ==============================================================================
# SLIDE 5: USP & SAFETY — "AI PROPOSES. THE SYSTEM DECIDES."
# ==============================================================================
def build_slide_5(prs):
    slide = create_blank_slide(prs)
    add_header(slide, "04. Unique Selling Proposition & Safety", "USP: AI Proposes. The System Decides.",
               "AI has reasoning authority, not financial authority. Hard deterministic gates protect every financial mutation.")

    # Main Safety Pipeline Card
    add_card(slide, Inches(0.8), Inches(1.9), Inches(11.7), Inches(3.3), bg_color=CARD_BG_ALT, border_color=BORDER_BLUE)
    
    tb_pipe = slide.shapes.add_textbox(Inches(1.1), Inches(2.05), Inches(11.1), Inches(3.0))
    tf_pipe = tb_pipe.text_frame
    
    p_ph = tf_pipe.paragraphs[0]
    p_ph.text = "THE DETERMINISTIC SAFETY BOUNDARY"
    p_ph.font.name = FONT_MAIN
    p_ph.font.size = Pt(12)
    p_ph.font.bold = True
    p_ph.font.color.rgb = CYAN_ACCENT

    p_flow = tf_pipe.add_paragraph()
    p_flow.text = (
        "\n1. AI AGENT REASONING LAYER (READ-ONLY)\n"
        "   Investigates Context  →  Evaluates Constraints  →  Outputs Structured Recommendation\n"
        "   ═══════════════════════════════════════════════════════════════════════════════════════\n"
        "   MANDATORY SAFETY BOUNDARY: FINANCIAL POLICY ENGINE\n"
        "   ═══════════════════════════════════════════════════════════════════════════════════════\n"
        "2. DETERMINISTIC AUTHORIZATION & EXECUTION LAYER\n"
        "   • Policy Gate Check: Validates retry limits, merchant SLA window, and allowed strategies\n"
        "   • Pre-Execution Revalidation: Verifies authoritative DB state (halts if already paid)\n"
        "   • Redis Distributed Lock: SET NX EX ensures exclusive worker authorization\n"
        "   • PostgreSQL Idempotency Barrier: UNIQUE(idempotency_key) guarantees exactly-once execution\n"
        "   • Outcome Verification: Reconciles settlement response & appends cryptographic audit trace"
    )
    p_flow.font.name = FONT_CODE
    p_flow.font.size = Pt(9.5)
    p_flow.font.color.rgb = TEXT_WHITE

    # 3 Foundational Pillars Below
    pillars = [
        ("🧠 INTELLIGENCE", "AI Handles Ambiguity", "Synthesizes multi-variable context across rails, merchants, and failure codes.", CYAN_ACCENT),
        ("🛡️ SAFETY", "Policies Control Money", "Deterministic rules independently verify every action. Zero hallucinated debits.", EMERALD_GREEN),
        ("⚙️ RELIABILITY", "Distributed Invariants", "Redis locks + DB idempotency eliminate race conditions and duplicate executions.", BLUE_LIGHT),
    ]

    card_w = Inches(3.7)
    card_h = Inches(1.4)
    start_x = Inches(0.8)
    gap_x = Inches(0.3)
    y_pil = Inches(5.45)

    for i, (title, sub, desc, col) in enumerate(pillars):
        cx = start_x + i * (card_w + gap_x)
        add_card(slide, cx, y_pil, card_w, card_h, bg_color=CARD_BG, border_color=BORDER_SUBTLE)
        tb = slide.shapes.add_textbox(cx + Inches(0.18), y_pil + Inches(0.12), card_w - Inches(0.36), card_h - Inches(0.24))
        tf = tb.text_frame
        p = tf.paragraphs[0]
        p.text = title
        p.font.name = FONT_MAIN
        p.font.size = Pt(11)
        p.font.bold = True
        p.font.color.rgb = col
        p2 = tf.add_paragraph()
        p2.text = sub
        p2.font.name = FONT_MAIN
        p2.font.size = Pt(10.5)
        p2.font.bold = True
        p2.font.color.rgb = TEXT_WHITE
        p3 = tf.add_paragraph()
        p3.text = desc
        p3.font.name = FONT_MAIN
        p3.font.size = Pt(9)
        p3.font.color.rgb = TEXT_DIM

    add_speaker_notes(slide,
        "Our core USP is simple: AI proposes. The system decides.\n\n"
        "AI has reasoning authority, not financial authority.\n\n"
        "When the agent recommends an action, that recommendation must cross a hard safety boundary.\n\n"
        "The financial policy engine independently verifies retry caps, merchant SLAs, and current payment state.\n\n"
        "Redis distributed locks prevent race conditions between workers, and the database idempotency barrier guarantees exactly-once execution.\n\n"
        "Intelligence handles ambiguity; deterministic systems guarantee safety."
    )

# ==============================================================================
# SLIDE 6: TECH STACK (ORGANIZED BY RESPONSIBILITY)
# ==============================================================================
def build_slide_6(prs):
    slide = create_blank_slide(prs)
    add_header(slide, "05. Technical Architecture", "Technology Stack: Organized by Responsibility",
               "A production-grade distributed architecture built for low-latency payment operations.")

    components = [
        ("EVENT BACKBONE", "Kafka Event Contracts", "Decoupled asynchronous event ingestion, resilient dead-letter queues.", CYAN_ACCENT),
        ("APPLICATION CORE", "Python 3.12 + FastAPI", "High-performance async ASGI runtime with strict Pydantic v2 validation.", BLUE_LIGHT),
        ("PERSISTENCE", "PostgreSQL 16", "ACID transactional state machine & immutable cryptographic audit ledger.", BLUE_VIBRANT),
        ("COORDINATION", "Redis 7 Distributed Locks", "Atomic SET NX EX locks with Lua release; prevents concurrent worker collisions.", AMBER_WARN),
        ("AI TEAMMATE", "LLM Gateway + Model Router", "Tiered model routing (5ms Fast Classifier vs 25ms Deep Reasoning Specialist).", EMERALD_GREEN),
        ("RELIABILITY & SAFETY", "Policy Engine + FSM", "Deterministic financial policy guards and recovery finite state machines.", RED_DANGER),
    ]

    card_w = Inches(3.7)
    card_h = Inches(1.8)
    start_x = Inches(0.8)
    gap_x = Inches(0.3)
    y1 = Inches(1.9)
    y2 = Inches(3.9)

    for i, (resp, tech, desc, col) in enumerate(components[:3]):
        cx = start_x + i * (card_w + gap_x)
        add_card(slide, cx, y1, card_w, card_h, bg_color=CARD_BG, border_color=BORDER_SUBTLE)
        tb = slide.shapes.add_textbox(cx + Inches(0.18), y1 + Inches(0.15), card_w - Inches(0.36), card_h - Inches(0.3))
        tf = tb.text_frame
        p = tf.paragraphs[0]
        p.text = resp
        p.font.name = FONT_MAIN
        p.font.size = Pt(10)
        p.font.bold = True
        p.font.color.rgb = col
        p_t = tf.add_paragraph()
        p_t.text = tech
        p_t.font.name = FONT_MAIN
        p_t.font.size = Pt(13)
        p_t.font.bold = True
        p_t.font.color.rgb = TEXT_WHITE
        p_d = tf.add_paragraph()
        p_d.text = desc
        p_d.font.name = FONT_MAIN
        p_d.font.size = Pt(9.5)
        p_d.font.color.rgb = TEXT_DIM

    for i, (resp, tech, desc, col) in enumerate(components[3:]):
        cx = start_x + i * (card_w + gap_x)
        add_card(slide, cx, y2, card_w, card_h, bg_color=CARD_BG, border_color=BORDER_SUBTLE)
        tb = slide.shapes.add_textbox(cx + Inches(0.18), y2 + Inches(0.15), card_w - Inches(0.36), card_h - Inches(0.3))
        tf = tb.text_frame
        p = tf.paragraphs[0]
        p.text = resp
        p.font.name = FONT_MAIN
        p.font.size = Pt(10)
        p.font.bold = True
        p.font.color.rgb = col
        p_t = tf.add_paragraph()
        p_t.text = tech
        p_t.font.name = FONT_MAIN
        p_t.font.size = Pt(13)
        p_t.font.bold = True
        p_t.font.color.rgb = TEXT_WHITE
        p_d = tf.add_paragraph()
        p_d.text = desc
        p_d.font.name = FONT_MAIN
        p_d.font.size = Pt(9.5)
        p_d.font.color.rgb = TEXT_DIM

    # Bottom Verification Strip
    v_card = add_card(slide, Inches(0.8), Inches(5.95), Inches(11.7), Inches(0.9), bg_color=CARD_BG_ALT, border_color=EMERALD_GREEN)
    tb_v = slide.shapes.add_textbox(Inches(1.1), Inches(6.05), Inches(11.1), Inches(0.7))
    tf_v = tb_v.text_frame
    p_v = tf_v.paragraphs[0]
    p_v.text = "VERIFIED PRODUCTION CODEBASE: 120 / 120 Pytest test suites passing across unit, integration, FSM, concurrency, and AI gateway modules."
    p_v.font.name = FONT_MAIN
    p_v.font.size = Pt(10.5)
    p_v.font.bold = True
    p_v.font.color.rgb = TEXT_WHITE

    add_speaker_notes(slide,
        "Rather than a generic technology logo dump, we organized our tech stack by architectural responsibility.\n\n"
        "Kafka serves as our decoupled event backbone. FastAPI and Python 3.12 provide the async non-blocking application core.\n\n"
        "PostgreSQL maintains transactional state and immutable audit traces. Redis coordinates distributed locking to prevent worker race conditions.\n\n"
        "Our AI teammate is served through a tiered model gateway with L7 load balancing.\n\n"
        "And our deterministic policy engine enforces hard safety invariants. All validated across 120 automated tests."
    )

# ==============================================================================
# SLIDE 7: REAL SCENARIO — THE INR 75,000 HIGH-VALUE RECOVERY
# ==============================================================================
def build_slide_7(prs):
    slide = create_blank_slide(prs)
    add_header(slide, "06. Tangible Case Study", "Case Study: The ₹75,000 High-Value Recovery",
               "How the AI teammate investigates, reasons, and safely orchestrates a complex rail failover.")

    steps = [
        ("1. FAILURE DETECTED", "₹75,000 Payment Fails", "Attempt #2 fails on HDFC Cards with GATEWAY_ERROR. Rail health score plummets to 38%."),
        ("2. TEAMMATE INVESTIGATES", "6 Read-Only Tools", "IRO Agent activates. Reads payment context, attempt sequence, route health & merchant SLA."),
        ("3. REASON & RECOMMEND", "Route Switch Proposed", "Agent spots HDFC rail is failing, but ICICI is at 98.4% health. Recommends SWITCH_ROUTE."),
        ("4. POLICY GATE PASSES", "Deterministic Checks", "Financial policy checks: Attempt limit (2<3), SLA window valid, route permitted, payment still failed."),
        ("5. SAFE ORCHESTRATION", "Revenue Recovered!", "Redis lock acquired, idempotency reserved, payment captured on ICICI rail. ₹75,000 SAVED!"),
    ]

    card_w = Inches(2.18)
    card_h = Inches(4.5)
    gap_x = Inches(0.2)
    start_x = Inches(0.8)
    y = Inches(1.9)

    for i, (step_num, title, text) in enumerate(steps):
        cx = start_x + i * (card_w + gap_x)
        border_c = EMERALD_GREEN if i == 4 else (BLUE_VIBRANT if i == 2 else BORDER_SUBTLE)
        add_card(slide, cx, y, card_w, card_h, bg_color=CARD_BG, border_color=border_c)

        tb = slide.shapes.add_textbox(cx + Inches(0.15), y + Inches(0.2), card_w - Inches(0.3), card_h - Inches(0.4))
        tf = tb.text_frame
        tf.word_wrap = True

        p1 = tf.paragraphs[0]
        p1.text = step_num
        p1.font.name = FONT_MAIN
        p1.font.size = Pt(10)
        p1.font.bold = True
        p1.font.color.rgb = CYAN_ACCENT

        p2 = tf.add_paragraph()
        p2.text = title
        p2.font.name = FONT_MAIN
        p2.font.size = Pt(12)
        p2.font.bold = True
        p2.font.color.rgb = TEXT_WHITE

        p3 = tf.add_paragraph()
        p3.text = f"\n{text}"
        p3.font.name = FONT_MAIN
        p3.font.size = Pt(9.5)
        p3.font.color.rgb = TEXT_DIM

    add_speaker_notes(slide,
        "Now let's see how this works in an actual scenario.\n\n"
        "Imagine a seventy-five-thousand-rupee credit card payment that failed because its payment route degraded after multiple attempts.\n\n"
        "This isn't a simple retry. IRO detects the ambiguity and sends the case to its investigation layer.\n\n"
        "The agent inspects the history, previous attempts, route health, and merchant policy.\n\n"
        "It discovers the primary route is at 38% health, but an alternate route is at 98%. It recommends switching routes.\n\n"
        "The financial policy engine verifies all limits and state. The lock is acquired, idempotency reserved, and the payment is recovered.\n\n"
        "That is the difference between blindly retrying and having an intelligent teammate."
    )

# ==============================================================================
# SLIDE 8: IMPACT & BENEFITS — 100-CASE SYNTHETIC BENCHMARK
# ==============================================================================
def build_slide_8(prs):
    slide = create_blank_slide(prs)
    add_header(slide, "07. Empirical Results & Validation", "Impact & Benefits: 100-Case Synthetic Benchmark",
               "Rigorous empirical evaluation comparing IRO against a transparent naive single-rail baseline.")

    # 3 Giant Metric Cards
    metrics = [
        ("RECOVERY RATE", "57.0%", "32.0%", "+78.1% Relative Lift", EMERALD_GREEN),
        ("REVENUE RECOVERED", "₹231,069", "₹147,657", "+₹83,412 Net Lift (+56.5%)", BLUE_LIGHT),
        ("UNSAFE ACTIONS", "0 EXECUTED", "24 EXECUTED", "24 Unsafe Attempts Blocked", CYAN_ACCENT),
    ]

    card_w = Inches(3.7)
    card_h = Inches(2.2)
    gap_x = Inches(0.3)
    start_x = Inches(0.8)
    y = Inches(1.9)

    for i, (label, iro_val, base_val, lift, col) in enumerate(metrics):
        cx = start_x + i * (card_w + gap_x)
        add_card(slide, cx, y, card_w, card_h, bg_color=CARD_BG, border_color=BORDER_SUBTLE)

        tb = slide.shapes.add_textbox(cx + Inches(0.2), y + Inches(0.18), card_w - Inches(0.4), card_h - Inches(0.3))
        tf = tb.text_frame

        p_lbl = tf.paragraphs[0]
        p_lbl.text = label
        p_lbl.font.name = FONT_MAIN
        p_lbl.font.size = Pt(11)
        p_lbl.font.bold = True
        p_lbl.font.color.rgb = TEXT_MUTED

        p_iro = tf.add_paragraph()
        p_iro.text = f"IRO: {iro_val}"
        p_iro.font.name = FONT_MAIN
        p_iro.font.size = Pt(22)
        p_iro.font.bold = True
        p_iro.font.color.rgb = col

        p_base = tf.add_paragraph()
        p_base.text = f"Baseline: {base_val}"
        p_base.font.name = FONT_MAIN
        p_base.font.size = Pt(12)
        p_base.font.color.rgb = TEXT_DIM

        p_lift = tf.add_paragraph()
        p_lift.text = lift
        p_lift.font.name = FONT_MAIN
        p_lift.font.size = Pt(10.5)
        p_lift.font.bold = True
        p_lift.font.color.rgb = TEXT_WHITE

    # Cost & Efficiency Card
    eff_card = add_card(slide, Inches(0.8), Inches(4.35), Inches(11.7), Inches(1.8), bg_color=CARD_BG_ALT, border_color=AMBER_WARN)
    tb_eff = slide.shapes.add_textbox(Inches(1.1), Inches(4.5), Inches(11.1), Inches(1.5))
    tf_eff = tb_eff.text_frame

    p_eh = tf_eff.paragraphs[0]
    p_eh.text = "WORKLOAD DISTRIBUTION & AI INFERENCE ROI"
    p_eh.font.name = FONT_MAIN
    p_eh.font.size = Pt(11)
    p_eh.font.bold = True
    p_eh.font.color.rgb = AMBER_WARN

    p_ed = tf_eff.add_paragraph()
    p_ed.text = (
        "• 68 Deterministic Cases (Tier 1): Resolved instantly with ZERO AI token overhead (<1ms).\n"
        "• 32 Specialist Agent Cases (Tier 3): Deep investigation for genuine multi-variable ambiguity.\n"
        "• ₹28.56 ($0.336 USD) Total AI Cost  →  2,920x ROI Multiplier (₹2,920 revenue recovered per ₹1 spent on AI)."
    )
    p_ed.font.name = FONT_MAIN
    p_ed.font.size = Pt(10)
    p_ed.font.color.rgb = TEXT_WHITE

    # Disclaimer
    disclaimer_box = slide.shapes.add_textbox(Inches(0.8), Inches(6.3), Inches(11.7), Inches(0.4))
    tf_d = disclaimer_box.text_frame
    p_d = tf_d.paragraphs[0]
    p_d.text = "DISCLAIMER: Controlled 100-case synthetic benchmark with realistic distributions. Not real Razorpay production transaction data."
    p_d.font.name = FONT_MAIN
    p_d.font.size = Pt(9)
    p_d.font.italic = True
    p_d.font.color.rgb = TEXT_MUTED

    add_speaker_notes(slide,
        "We evaluated IRO across one hundred controlled synthetic payment scenarios.\n\n"
        "The results speak for themselves: IRO achieved a 57 percent overall recovery rate, compared with 32 percent for the naive baseline.\n\n"
        "That generated two hundred and thirty-one thousand rupees of recovered revenue, an incremental gain of over eighty-three thousand rupees.\n\n"
        "And most importantly: IRO executed zero unsafe actions while blocking twenty-four simulated violations.\n\n"
        "Total AI cost: just twenty-eight rupees, representing a 2,920x ROI."
    )

# ==============================================================================
# SLIDE 9: BUSINESS MODEL & CATEGORY EXPANSION
# ==============================================================================
def build_slide_9(prs):
    slide = create_blank_slide(prs)
    add_header(slide, "08. Market Opportunity & Monetization", "Business Model: Autonomous Payment Operations",
               "Positioning IRO as enterprise B2B fintech infrastructure for banks, PSPs, and high-volume merchants.")

    # 3 Revenue Model Pillars
    rev_tiers = [
        ("1. PLATFORM LICENSE", "Annual Enterprise Core", "License for core orchestrator, policy engine, dashboard console, and compliance ledger.", CYAN_ACCENT),
        ("2. USAGE-BASED TIERS", "Per-Operation Pricing", "Metered fee per ambiguous recovery case or automated payment operation processed.", BLUE_LIGHT),
        ("3. ENTERPRISE VPC", "Private Infrastructure", "Custom bank gateway integrations, private cloud VPC deployment, dedicated SLA.", BLUE_VIBRANT),
    ]

    card_w = Inches(3.7)
    card_h = Inches(2.2)
    start_x = Inches(0.8)
    gap_x = Inches(0.3)
    y = Inches(1.9)

    for i, (title, sub, desc, col) in enumerate(rev_tiers):
        cx = start_x + i * (card_w + gap_x)
        add_card(slide, cx, y, card_w, card_h, bg_color=CARD_BG, border_color=BORDER_SUBTLE)
        tb = slide.shapes.add_textbox(cx + Inches(0.18), y + Inches(0.18), card_w - Inches(0.36), card_h - Inches(0.3))
        tf = tb.text_frame
        p = tf.paragraphs[0]
        p.text = title
        p.font.name = FONT_MAIN
        p.font.size = Pt(11)
        p.font.bold = True
        p.font.color.rgb = col
        p2 = tf.add_paragraph()
        p2.text = sub
        p2.font.name = FONT_MAIN
        p2.font.size = Pt(13)
        p2.font.bold = True
        p2.font.color.rgb = TEXT_WHITE
        p3 = tf.add_paragraph()
        p3.text = f"\n{desc}"
        p3.font.name = FONT_MAIN
        p3.font.size = Pt(9.5)
        p3.font.color.rgb = TEXT_DIM

    # Category Expansion Roadmap Card
    cat_card = add_card(slide, Inches(0.8), Inches(4.35), Inches(11.7), Inches(2.3), bg_color=CARD_BG_ALT, border_color=BORDER_BLUE)
    tb_cat = slide.shapes.add_textbox(Inches(1.1), Inches(4.5), Inches(11.1), Inches(2.0))
    tf_cat = tb_cat.text_frame

    p_ch = tf_cat.paragraphs[0]
    p_ch.text = "CATEGORY EVOLUTION: FROM RECOVERY TO FULL PAYMENT OPERATIONS"
    p_ch.font.name = FONT_MAIN
    p_ch.font.size = Pt(11)
    p_ch.font.bold = True
    p_ch.font.color.rgb = CYAN_ACCENT

    p_cd = tf_cat.add_paragraph()
    p_cd.text = (
        "• TODAY (Built & Verified): Failure Investigation  →  Recovery Orchestration  →  Outcome Verification\n\n"
        "• FUTURE EXPANSION ROADMAP:\n"
        "  - Autonomous Reconciliation: Resolves in-flight timeouts & bank pending states without human tickets\n"
        "  - Dynamic Route Health Balancing: Preemptively shifts traffic away from failing banking rails\n"
        "  - Merchant SLA Guardrails: Automatically tunes retry limits to merchant margin & risk thresholds\n"
        "  - Operational Escalation: Human-in-the-loop copilot for regulatory reviews and high-value chargebacks"
    )
    p_cd.font.name = FONT_MAIN
    p_cd.font.size = Pt(9.5)
    p_cd.font.color.rgb = TEXT_DIM

    add_speaker_notes(slide,
        "Our business model positions IRO as B2B fintech infrastructure for banks, payment aggregators, and enterprise merchants.\n\n"
        "It combines an annual platform license with usage-based pricing for recovered volume.\n\n"
        "The value proposition is clear: recovered revenue, reduced operational workload, and zero unsafe automations.\n\n"
        "Today, IRO masters payment failure investigation and recovery.\n\n"
        "Tomorrow, this autonomous teammate can expand into reconciliation, route rebalancing, and operational dispute management."
    )

# ==============================================================================
# SLIDE 10: CLOSING — "THE TEAMMATE THAT MAKES THEM SMARTER"
# ==============================================================================
def build_slide_10(prs):
    slide = create_blank_slide(prs)

    # Hero Quote Card (Full Width)
    hero_card = add_card(slide, Inches(0.8), Inches(1.4), Inches(11.7), Inches(3.8), bg_color=CARD_BG_ALT, border_color=BLUE_VIBRANT)
    tb_h = slide.shapes.add_textbox(Inches(1.2), Inches(1.8), Inches(10.9), Inches(3.0))
    tf_h = tb_h.text_frame

    p_h1 = tf_h.paragraphs[0]
    p_h1.text = "“AI shouldn't replace payment operations.”"
    p_h1.font.name = FONT_MAIN
    p_h1.font.size = Pt(22)
    p_h1.font.italic = True
    p_h1.font.color.rgb = TEXT_DIM

    p_h2 = tf_h.add_paragraph()
    p_h2.text = "“It should become the teammate that makes them smarter.”"
    p_h2.font.name = FONT_MAIN
    p_h2.font.size = Pt(32)
    p_h2.font.bold = True
    p_h2.font.color.rgb = TEXT_WHITE

    p_h3 = tf_h.add_paragraph()
    p_h3.text = "\nIRO — The Autonomous AI Teammate for Payment Operations\nInvestigate.  Reason.  Orchestrate.  Verify."
    p_h3.font.name = FONT_CODE
    p_h3.font.size = Pt(14)
    p_h3.font.bold = True
    p_h3.font.color.rgb = CYAN_ACCENT

    # Bottom Metadata Strip
    meta_card = add_card(slide, Inches(0.8), Inches(5.6), Inches(11.7), Inches(1.2), bg_color=CARD_BG, border_color=BORDER_SUBTLE)
    tb_m = slide.shapes.add_textbox(Inches(1.1), Inches(5.75), Inches(11.1), Inches(0.9))
    tf_m = tb_m.text_frame
    p_m1 = tf_m.paragraphs[0]
    p_m1.text = "RAZORPAY AI BUILDATHON 2026 • TRACK 3: AUTONOMOUS AI TEAMMATES"
    p_m1.font.name = FONT_MAIN
    p_m1.font.size = Pt(11)
    p_m1.font.bold = True
    p_m1.font.color.rgb = BLUE_LIGHT

    p_m2 = tf_m.add_paragraph()
    p_m2.text = "Author: Om Dapke  •  github.com/omdapke01/intelligent-recovery-orchestrator  •  120 / 120 Pytest Passing"
    p_m2.font.name = FONT_MAIN
    p_m2.font.size = Pt(10.5)
    p_m2.font.color.rgb = TEXT_DIM

    add_speaker_notes(slide,
        "Our biggest learning from building IRO is that payment operations isn't just an AI problem.\n\n"
        "It's a reliability problem.\n"
        "A concurrency problem.\n"
        "A policy problem.\n"
        "A cost problem.\n"
        "And only sometimes… an AI problem.\n\n"
        "AI shouldn't replace payment operations. It should become the teammate that makes them smarter.\n\n"
        "IRO — The Autonomous AI Teammate for Payment Operations.\n"
        "Investigate. Reason. Orchestrate. Verify.\n\n"
        "Thank you."
    )

# ==============================================================================
# MAIN EXECUTION ENTRY POINT
# ==============================================================================
def main():
    print("Initializing PowerPoint presentation...")
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    print("Building Slide 1: Title (Autonomous AI Teammate)...")
    build_slide_1(prs)

    print("Building Slide 2: Problem Statement (A failed payment isn't necessarily a failed transaction)...")
    build_slide_2(prs)

    print("Building Slide 3: Proposed Solution (Meet IRO: 5 Responsibilities)...")
    build_slide_3(prs)

    print("Building Slide 4: How the AI Teammate Works (6 Bounded Read-Only Tools)...")
    build_slide_4(prs)

    print("Building Slide 5: USP & Safety (AI Proposes. The System Decides)...")
    build_slide_5(prs)

    print("Building Slide 6: Technology Stack (Organized by Responsibility)...")
    build_slide_6(prs)

    print("Building Slide 7: Real Scenario (The INR 75,000 High-Value Recovery)...")
    build_slide_7(prs)

    print("Building Slide 8: Impact & Benefits (100-Case Synthetic Benchmark)...")
    build_slide_8(prs)

    print("Building Slide 9: Business Model (Autonomous Payment Operations Platform)...")
    build_slide_9(prs)

    print("Building Slide 10: Closing (The Teammate That Makes Them Smarter)...")
    build_slide_10(prs)

    output_path = "IRO_Razorpay_Pitch_Deck.pptx"
    prs.save(output_path)
    print(f"Presentation successfully saved to: {os.path.abspath(output_path)}")

if __name__ == "__main__":
    main()
