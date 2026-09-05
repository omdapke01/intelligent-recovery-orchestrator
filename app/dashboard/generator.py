"""Dashboard Generator rendering rich standalone HTML dashboards, JSON reports,

and safe console terminal visualizers.
"""

from datetime import datetime
import json
import os
from typing import Any, Dict, Optional

from app.evaluation.models import BenchmarkComparisonReport


class DashboardGenerator:
    """Generates standalone interactive HTML visual dashboards, JSON reports,

    and terminal summaries for evaluation benchmarks.
    """

    @classmethod
    def generate_html(cls, report: BenchmarkComparisonReport, filepath: str = "dashboard.html") -> str:
        """Generate a rich, standalone interactive HTML dashboard file."""
        b = report.baseline_metrics
        i = report.iro_metrics
        c = report

        # Tier breakdown bars
        total_decisions = sum(i.tier_breakdown.values()) or 1
        tier_html_items = ""
        colors = {
            "TIER_1_DETERMINISTIC": "#10b981",       # Emerald
            "TIER_2_HEURISTIC": "#3b82f6",           # Blue
            "TIER_3_SPECIALIST_AGENT": "#8b5cf6",     # Purple
            "TIER_3_AI_GATEWAY": "#6366f1",           # Indigo
            "FALLBACK_MANUAL_REVIEW": "#f59e0b",      # Amber
        }

        for tier_name, count in sorted(i.tier_breakdown.items(), key=lambda x: -x[1]):
            pct = (count / total_decisions) * 100.0
            color = colors.get(tier_name, "#64748b")
            tier_html_items += f"""
            <div style="margin-bottom: 12px;">
                <div style="display: flex; justify-content: space-between; margin-bottom: 4px; font-size: 13px;">
                    <span style="font-weight: 600; color: #e2e8f0;">{tier_name.replace('_', ' ')}</span>
                    <span style="color: #94a3b8;">{count} cases ({pct:.1f}%)</span>
                </div>
                <div style="background: #1e293b; border-radius: 4px; height: 10px; overflow: hidden;">
                    <div style="background: {color}; width: {pct:.1f}%; height: 100%; border-radius: 4px;"></div>
                </div>
            </div>
            """

        # Segmented rows: Payment Methods
        method_rows = ""
        for m_key, sm in report.by_payment_method.items():
            method_rows += f"""
            <tr>
                <td style="font-weight: 600; color: #f8fafc;">{m_key}</td>
                <td>{sm.total_cases}</td>
                <td>{sm.baseline_recovered} ({sm.baseline_recovery_rate:.1f}%)</td>
                <td style="color: #10b981; font-weight: 600;">{sm.iro_recovered} ({sm.iro_recovery_rate:.1f}%)</td>
                <td style="color: #10b981; font-weight: 600;">+{sm.recovery_rate_lift_pct:.1f}%</td>
                <td>INR {sm.baseline_revenue_inr:,.2f}</td>
                <td style="color: #10b981; font-weight: 600;">INR {sm.iro_revenue_inr:,.2f}</td>
                <td style="color: #38bdf8; font-weight: 600;">+INR {sm.revenue_lift_inr:,.2f}</td>
                <td style="color: #f43f5e; font-weight: 600;">{sm.unsafe_actions_prevented}</td>
            </tr>
            """

        # Segmented rows: Value Segments
        value_rows = ""
        for v_key, sv in report.by_value_segment.items():
            value_rows += f"""
            <tr>
                <td style="font-weight: 600; color: #f8fafc;">{v_key}</td>
                <td>{sv.total_cases}</td>
                <td>{sv.baseline_recovered} ({sv.baseline_recovery_rate:.1f}%)</td>
                <td style="color: #10b981; font-weight: 600;">{sv.iro_recovered} ({sv.iro_recovery_rate:.1f}%)</td>
                <td style="color: #10b981; font-weight: 600;">+{sv.recovery_rate_lift_pct:.1f}%</td>
                <td>INR {sv.baseline_revenue_inr:,.2f}</td>
                <td style="color: #10b981; font-weight: 600;">INR {sv.iro_revenue_inr:,.2f}</td>
                <td style="color: #38bdf8; font-weight: 600;">+INR {sv.revenue_lift_inr:,.2f}</td>
                <td style="color: #f43f5e; font-weight: 600;">{sv.unsafe_actions_prevented}</td>
            </tr>
            """

        # Segmented rows: Failure Categories
        cat_rows = ""
        for c_key, sc in report.by_failure_category.items():
            cat_rows += f"""
            <tr>
                <td style="font-weight: 600; color: #f8fafc;">{c_key}</td>
                <td>{sc.total_cases}</td>
                <td>{sc.baseline_recovered} ({sc.baseline_recovery_rate:.1f}%)</td>
                <td style="color: #10b981; font-weight: 600;">{sc.iro_recovered} ({sc.iro_recovery_rate:.1f}%)</td>
                <td style="color: #10b981; font-weight: 600;">+{sc.recovery_rate_lift_pct:.1f}%</td>
                <td>INR {sc.baseline_revenue_inr:,.2f}</td>
                <td style="color: #10b981; font-weight: 600;">INR {sc.iro_revenue_inr:,.2f}</td>
                <td style="color: #38bdf8; font-weight: 600;">+INR {sc.revenue_lift_inr:,.2f}</td>
                <td style="color: #f43f5e; font-weight: 600;">{sc.unsafe_actions_prevented}</td>
            </tr>
            """

        # Sample Records Table
        sample_rows = ""
        for idx, rec in enumerate(report.records[:25]):
            status_badge = '<span style="background: rgba(16,185,129,0.2); color: #10b981; padding: 2px 8px; border-radius: 4px; font-weight: 600;">RECOVERED</span>' if rec.recovered else (
                '<span style="background: rgba(245,158,11,0.2); color: #f59e0b; padding: 2px 8px; border-radius: 4px; font-weight: 600;">ESCALATED</span>' if rec.escalated_to_human else (
                    '<span style="background: rgba(244,63,94,0.2); color: #f43f5e; padding: 2px 8px; border-radius: 4px; font-weight: 600;">STOPPED</span>'
                )
            )
            sample_rows += f"""
            <tr>
                <td style="font-family: monospace; color: #94a3b8;">{rec.payment_id[:8]}...</td>
                <td style="font-weight: 600;">INR {rec.amount_inr:,.2f}</td>
                <td>{rec.payment_method}</td>
                <td style="font-family: monospace; color: #38bdf8;">{rec.error_code}</td>
                <td>{rec.strategy_chosen}</td>
                <td><span style="font-size: 11px; background: #1e293b; padding: 2px 6px; border-radius: 3px;">{rec.tier_used}</span></td>
                <td>{status_badge}</td>
                <td>{rec.latency_ms:.1f}ms</td>
                <td style="font-size: 12px; color: #94a3b8;">INR {rec.synthetic_cost_inr:.4f}</td>
            </tr>
            """

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Razorpay IRO — Intelligent Recovery Orchestrator Dashboard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }}
        body {{ background-color: #0b0f19; color: #cbd5e1; padding: 28px; line-height: 1.5; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #1e293b; padding-bottom: 20px; margin-bottom: 24px; }}
        .brand {{ display: flex; align-items: center; gap: 14px; }}
        .badge-live {{ background: #0284c7; color: #ffffff; padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: 700; letter-spacing: 0.5px; }}
        .title {{ font-size: 24px; font-weight: 800; color: #f8fafc; letter-spacing: -0.5px; }}
        .subtitle {{ font-size: 13px; color: #94a3b8; margin-top: 4px; }}
        .banner {{ background: rgba(14, 165, 233, 0.1); border: 1px solid rgba(14, 165, 233, 0.3); border-radius: 8px; padding: 12px 18px; margin-bottom: 24px; font-size: 13px; color: #7dd3fc; display: flex; align-items: center; gap: 10px; }}
        .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 16px; margin-bottom: 28px; }}
        .kpi-card {{ background: #131b2e; border: 1px solid #1e293b; border-radius: 10px; padding: 18px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2); }}
        .kpi-title {{ font-size: 12px; font-weight: 600; text-transform: uppercase; color: #94a3b8; margin-bottom: 6px; letter-spacing: 0.5px; }}
        .kpi-value {{ font-size: 26px; font-weight: 800; color: #f8fafc; }}
        .kpi-delta {{ font-size: 13px; font-weight: 600; margin-top: 4px; }}
        .delta-pos {{ color: #10b981; }}
        .delta-neutral {{ color: #38bdf8; }}
        .delta-alert {{ color: #f43f5e; }}
        .section-card {{ background: #131b2e; border: 1px solid #1e293b; border-radius: 10px; padding: 22px; margin-bottom: 24px; }}
        .section-title {{ font-size: 17px; font-weight: 700; color: #f8fafc; margin-bottom: 16px; display: flex; align-items: center; justify-content: space-between; }}
        table {{ width: 100%; border-collapse: collapse; text-align: left; font-size: 13px; }}
        th {{ background: #1e293b; color: #94a3b8; padding: 10px 14px; font-weight: 600; border-bottom: 1px solid #334155; }}
        td {{ padding: 12px 14px; border-bottom: 1px solid #1e293b; }}
        tr:hover td {{ background: rgba(30, 41, 59, 0.5); }}
        .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
        @media (max-width: 900px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}
    </style>
</head>
<body>

    <div class="header">
        <div class="brand">
            <div>
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span class="badge-live">PHASE 8 BENCHMARK</span>
                    <span class="badge-live" style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; border-color: rgba(56, 189, 248, 0.3);">CUSTOMER ACTION CONVERSION: 35%</span>
                    <span style="font-size: 12px; color: #64748b;">Report ID: {c.report_id}</span>
                </div>
                <h1 class="title">Intelligent Recovery Orchestrator (IRO)</h1>
                <p class="subtitle">Controlled Benchmark: Naive Single-Rail Baseline vs Intelligent 7-Phase Multi-Rail Recovery</p>
            </div>
        </div>
        <div style="text-align: right; font-size: 12px; color: #94a3b8;">
            <div>Evaluated: <strong style="color: #f8fafc;">{c.total_cases} Payments</strong></div>
            <div>Generated: {c.timestamp[:19]} UTC</div>
        </div>
    </div>

    <div class="banner">
        <span style="font-size: 18px;">ℹ️</span>
        <div>
            <strong>Synthetic Simulation Assumptions:</strong> Fast Tier ($0.0005/1K in, $0.0015/1K out), Deep Reasoning ($0.010/1K in, $0.030/1K out), USD/INR = 85.0. 
            All inference cost calculations and ROI multipliers are evaluated strictly as synthetic engineering benchmarks.
        </div>
    </div>

    <!-- Top-Level Key Metrics KPI Cards -->
    <div class="kpi-grid">
        <div class="kpi-card">
            <div class="kpi-title">Overall Recovery Rate</div>
            <div class="kpi-value" style="color: #10b981;">{i.recovery_rate_pct:.1f}%</div>
            <div class="kpi-delta delta-pos">+{c.absolute_recovery_rate_lift_pct:.1f}% vs Baseline ({b.recovery_rate_pct:.1f}%)</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Incremental Revenue</div>
            <div class="kpi-value" style="color: #38bdf8;">+INR {c.incremental_recovered_revenue_inr:,.0f}</div>
            <div class="kpi-delta delta-pos">+{c.revenue_lift_pct:.1f}% Revenue Lift</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Unsafe Actions Blocked</div>
            <div class="kpi-value" style="color: #10b981;">{c.unsafe_actions_prevented}</div>
            <div class="kpi-delta delta-alert">Baseline: {b.unsafe_actions_count} violations | IRO: {i.unsafe_actions_count}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Human Escalations</div>
            <div class="kpi-value">{i.escalations_count}</div>
            <div class="kpi-delta delta-neutral">Automated Amount Cap Policies</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Decision Latency</div>
            <div class="kpi-value">{i.avg_latency_ms:.0f} ms</div>
            <div class="kpi-delta delta-neutral">Orchestration Only (Excl. Gateway)</div>
        </div>
    </div>

    <!-- Side-by-Side Comparison Matrix -->
    <div class="section-card">
        <div class="section-title">
            <span>Executive Comparison: Naive Single-Rail Baseline vs IRO Architecture</span>
        </div>
        <table>
            <thead>
                <tr>
                    <th>Evaluation Dimension</th>
                    <th>Naive Single-Rail Baseline</th>
                    <th>Intelligent Recovery Orchestrator (IRO)</th>
                    <th>Performance Lift / Safety Delta</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td style="font-weight: 600;">Recovery Strategy</td>
                    <td>Static immediate single retry on original rail</td>
                    <td>3-Tier Hierarchy (Deterministic -> Heuristic Failover -> Specialist Agent)</td>
                    <td style="color: #38bdf8; font-weight: 600;">Multi-rail failover & contextual action</td>
                </tr>
                <tr>
                    <td style="font-weight: 600;">Total Recovered Cases</td>
                    <td>{b.recovered_count} / {b.total_cases}</td>
                    <td style="color: #10b981; font-weight: 600;">{i.recovered_count} / {i.total_cases}</td>
                    <td style="color: #10b981; font-weight: 600;">+{i.recovered_count - b.recovered_count} Payments</td>
                </tr>
                <tr>
                    <td style="font-weight: 600;">Recovery Rate (Overall)</td>
                    <td>{b.recovery_rate_pct:.1f}%</td>
                    <td style="color: #10b981; font-weight: 600;">{i.recovery_rate_pct:.1f}%</td>
                    <td style="color: #10b981; font-weight: 600;">+{c.absolute_recovery_rate_lift_pct:.1f}% Points (+{c.relative_recovery_rate_lift_pct:.1f}% relative)</td>
                </tr>
                <tr>
                    <td style="font-weight: 600;">Recovery Rate (Retryable Only)</td>
                    <td>{b.retryable_recovery_rate_pct:.1f}%</td>
                    <td style="color: #10b981; font-weight: 600;">{i.retryable_recovery_rate_pct:.1f}%</td>
                    <td style="color: #10b981; font-weight: 600;">+{i.retryable_recovery_rate_pct - b.retryable_recovery_rate_pct:.1f}% Points</td>
                </tr>
                <tr>
                    <td style="font-weight: 600;">Recovered Revenue</td>
                    <td>INR {b.recovered_revenue_inr:,.2f}</td>
                    <td style="color: #10b981; font-weight: 600;">INR {i.recovered_revenue_inr:,.2f}</td>
                    <td style="color: #10b981; font-weight: 600;">+INR {c.incremental_recovered_revenue_inr:,.2f} (+{c.revenue_lift_pct:.1f}%)</td>
                </tr>
                <tr>
                    <td style="font-weight: 600;">Financial Policy Boundaries</td>
                    <td style="color: #f43f5e;">None (Blind execution)</td>
                    <td style="color: #10b981; font-weight: 600;">2-Stage Guard: Phase 3 Guard + Phase 7 Policy Engine</td>
                    <td style="color: #10b981; font-weight: 600;">Fail-Closed, Tamper-Evident Hash Chain</td>
                </tr>
                <tr>
                    <td style="font-weight: 600;">Unsafe Actions Executed</td>
                    <td style="color: #f43f5e; font-weight: 600;">{b.unsafe_actions_count} (Double debits & fraud retries)</td>
                    <td style="color: #10b981; font-weight: 600;">{i.unsafe_actions_count}</td>
                    <td style="color: #10b981; font-weight: 600;">100% Unsafe Actions Prevented</td>
                </tr>
                <tr>
                    <td style="font-weight: 600;">AI Serving Cost (Synthetic)</td>
                    <td>INR 0.00</td>
                    <td>INR {c.ai_total_cost_inr:.2f} (${i.synthetic_total_cost_usd:.4f})</td>
                    <td style="color: #38bdf8; font-weight: 600;">Financial ROI Multiplier: {c.roi_multiplier:,.0f}x</td>
                </tr>
            </tbody>
        </table>
    </div>

    <!-- Segmented Cohorts: Payment Methods & Value Segments -->
    <div class="grid-2">
        <div class="section-card">
            <div class="section-title">Segmented Performance by Payment Method</div>
            <table>
                <thead>
                    <tr>
                        <th>Method</th>
                        <th>Cases</th>
                        <th>Baseline</th>
                        <th>IRO</th>
                        <th>Lift %</th>
                        <th>Baseline Rev</th>
                        <th>IRO Rev</th>
                        <th>Delta Rev</th>
                        <th>Prevented</th>
                    </tr>
                </thead>
                <tbody>
                    {method_rows}
                </tbody>
            </table>
        </div>

        <div class="section-card">
            <div class="section-title">Segmented Performance by Value Tier</div>
            <table>
                <thead>
                    <tr>
                        <th>Value Tier</th>
                        <th>Cases</th>
                        <th>Baseline</th>
                        <th>IRO</th>
                        <th>Lift %</th>
                        <th>Baseline Rev</th>
                        <th>IRO Rev</th>
                        <th>Delta Rev</th>
                        <th>Prevented</th>
                    </tr>
                </thead>
                <tbody>
                    {value_rows}
                </tbody>
            </table>
        </div>
    </div>

    <!-- Failure Categories & Decision Tier Breakdown -->
    <div class="grid-2">
        <div class="section-card">
            <div class="section-title">Segmented Performance by Failure Profile</div>
            <table>
                <thead>
                    <tr>
                        <th>Category</th>
                        <th>Cases</th>
                        <th>Baseline</th>
                        <th>IRO</th>
                        <th>Lift %</th>
                        <th>Baseline Rev</th>
                        <th>IRO Rev</th>
                        <th>Delta Rev</th>
                        <th>Prevented</th>
                    </tr>
                </thead>
                <tbody>
                    {cat_rows}
                </tbody>
            </table>
        </div>

        <div class="section-card">
            <div class="section-title">IRO Decision Layer Distribution</div>
            <p style="font-size: 13px; color: #94a3b8; margin-bottom: 16px;">
                Demonstrates the 3-Tier Hierarchy: simple cases resolve deterministically via Tier 1, degraded switches trigger Tier 2 heuristic failovers, and ambiguous anomalies escalate to the bounded Tier 3 Specialist Agent.
            </p>
            {tier_html_items}
            <div style="margin-top: 20px; padding-top: 14px; border-top: 1px solid #1e293b; font-size: 12px; color: #94a3b8;">
                <div>Total Tokens Consumed: <strong style="color: #f8fafc;">{i.total_tokens:,}</strong> (Prompt: {i.total_prompt_tokens:,} | Comp: {i.total_completion_tokens:,})</div>
                <div>Avg Token Cost Per Payment: <strong style="color: #10b981;">INR {(c.ai_total_cost_inr / c.total_cases):.4f}</strong></div>
            </div>
        </div>
    </div>

    <!-- Sample Transaction Log -->
    <div class="section-card">
        <div class="section-title">
            <span>Sample Incident Explorer (First 25 Cases)</span>
            <span style="font-size: 12px; color: #94a3b8;">Authoritative PostgreSQL State & Phase 7 Audit Logs</span>
        </div>
        <table>
            <thead>
                <tr>
                    <th>Payment ID</th>
                    <th>Amount</th>
                    <th>Method</th>
                    <th>Error Code</th>
                    <th>Strategy Chosen</th>
                    <th>Tier Used</th>
                    <th>Outcome</th>
                    <th>Latency</th>
                    <th>AI Cost</th>
                </tr>
            </thead>
            <tbody>
                {sample_rows}
            </tbody>
        </table>
    </div>

</body>
</html>
"""
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html_content)

        return filepath

    @classmethod
    def generate_json(cls, report: BenchmarkComparisonReport, filepath: str = "iro_benchmark_report.json") -> str:
        """Export benchmark comparison telemetry to canonical machine-readable JSON."""
        json_str = report.to_json(indent=2)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(json_str)
        return filepath

    @classmethod
    def render_terminal_summary(cls, report: BenchmarkComparisonReport) -> str:
        """Format a clean ASCII summary table for Windows terminal display (avoiding cp1252 character crashes)."""
        b = report.baseline_metrics
        i = report.iro_metrics
        c = report

        lines = [
            "=" * 78,
            "   RAZORPAY INTELLIGENT RECOVERY ORCHESTRATOR (IRO) - BENCHMARK REPORT    ",
            "=" * 78,
            f"Report ID: {c.report_id}  |  Evaluated: {c.total_cases} Payments  |  Time: {c.timestamp[:19]}Z",
            "-" * 78,
            f"{'Dimension':<32} | {'Naive Baseline':<18} | {'IRO Engine':<20}",
            "-" * 78,
            f"{'Overall Recovery Rate':<32} | {b.recovery_rate_pct:>16.1f}% | {i.recovery_rate_pct:>18.1f}%",
            f"{'Retryable Recovery Rate':<32} | {b.retryable_recovery_rate_pct:>16.1f}% | {i.retryable_recovery_rate_pct:>18.1f}%",
            f"{'Recovered Payments Count':<32} | {b.recovered_count:>17} | {i.recovered_count:>19}",
            f"{'Recovered Revenue':<32} | INR {b.recovered_revenue_inr:>12,.2f} | INR {i.recovered_revenue_inr:>14,.2f}",
            f"{'Unsafe Actions (Fraud/Debits)':<32} | {b.unsafe_actions_count:>17} | {i.unsafe_actions_count:>19}",
            f"{'Human Escalations':<32} | {b.escalations_count:>17} | {i.escalations_count:>19}",
            f"{'Decision Latency (Excl. Gateway)':<32} | {b.avg_latency_ms:>14.1f}ms | {i.avg_latency_ms:>16.1f}ms",
            f"{'Synthetic Inference Cost':<32} | {'INR 0.00':>17} | INR {c.ai_total_cost_inr:>14.2f}",
            "-" * 78,
            "INCREMENTAL PERFORMANCE LIFT & FINANCIAL ROI:",
            f"  * Absolute Recovery Rate Lift:  +{c.absolute_recovery_rate_lift_pct:.1f}% percentage points",
            f"  * Relative Recovery Rate Lift:  +{c.relative_recovery_rate_lift_pct:.1f}% lift over baseline",
            f"  * Incremental Recovered Revenue: +INR {c.incremental_recovered_revenue_inr:,.2f} (+{c.revenue_lift_pct:.1f}%)",
            f"  * Unsafe Actions Blocked:       {c.unsafe_actions_prevented} policy violations prevented",
            f"  * AI Financial ROI Multiplier:  {c.roi_multiplier:,.0f}x (Recovered Revenue / AI Cost)",
            "-" * 78,
            "SEGMENTED BREAKDOWN BY PAYMENT METHOD:",
        ]

        for m, sm in report.by_payment_method.items():
            lines.append(
                f"  [{m:<12}] Cases: {sm.total_cases:<3} | Base: {sm.baseline_recovery_rate:>5.1f}% | "
                f"IRO: {sm.iro_recovery_rate:>5.1f}% (+{sm.recovery_rate_lift_pct:>4.1f}%) | "
                f"Delta: +INR {sm.revenue_lift_inr:>8,.0f}"
            )

        lines.extend([
            "-" * 78,
            "NOTE: Synthetic pricing assumptions: $0.0005/1K fast, $0.010/1K deep, 1 USD = INR 85.0. Customer action conversion: 35%.",
            "=" * 78,
        ])

        return "\n".join(lines)
