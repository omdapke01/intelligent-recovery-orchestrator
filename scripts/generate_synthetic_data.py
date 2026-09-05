"""CLI script to generate and export synthetic payment datasets across configurable scales."""

import argparse
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.synthetic.exporter import calculate_dataset_statistics, export_to_ndjson
from app.synthetic.generator import SyntheticPaymentGenerator


def main():
    parser = argparse.ArgumentParser(
        description="Generate realistic synthetic payment data for IRO revenue recovery."
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1000,
        help="Number of payments to generate (e.g. 10, 100, 1000, 10000, 100000)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/synthetic_payments.jsonl",
        help="Target output NDJSON file path",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility",
    )
    parser.add_argument(
        "--success-rate",
        type=float,
        default=0.78,
        help="Base initial success probability (default 0.78)",
    )

    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        choices=["healthy-transient", "degraded-route", "customer-action", "repeated-failure", "fraud-stop", "max-retries"],
        help="Deterministic scenario preset to generate (e.g. healthy-transient, degraded-route, customer-action, repeated-failure, fraud-stop, max-retries)",
    )

    args = parser.parse_args()

    generator = SyntheticPaymentGenerator(
        seed=args.seed,
        base_success_rate=args.success_rate,
    )

    start_time = time.perf_counter()

    if args.scenario:
        print(f"[*] Generating {args.count:,} payments for scenario preset '{args.scenario}'...")
        stream = (generator.generate_scenario(args.scenario, index=i) for i in range(args.count))
    else:
        print(f"[*] Initializing Synthetic Payment Generator (seed={args.seed}, target_count={args.count:,})...")
        print(f"[*] Streaming {args.count:,} payment transactions to {args.output}...")
        stream = generator.generate_stream(args.count)

    # Export to NDJSON streaming
    exported = export_to_ndjson(stream, args.output)
    duration = time.perf_counter() - start_time
    throughput = exported / duration if duration > 0 else 0

    print(f"[+] Successfully generated and exported {exported:,} payments in {duration:.2f}s ({throughput:,.1f} payments/sec).")

    # Re-run a sample or compute stats for display
    print("[*] Generating statistical audit report for generated batch...")
    if args.scenario:
        sample_stats = calculate_dataset_statistics(
            generator.generate_scenario(args.scenario, index=i) for i in range(min(args.count, 5000))
        )
    else:
        sample_stats = calculate_dataset_statistics(generator.generate_stream(min(args.count, 5000)))

    print("\n" + "=" * 50)
    print("       IRO SYNTHETIC DATASET AUDIT SUMMARY")
    print("=" * 50)
    print(f"  Total Payments Evaluated  : {sample_stats['total_payments']:,}")
    print(f"  Total Gross Volume (INR)  : INR {sample_stats['total_volume_inr']:,.2f}")
    print(f"  Initial Success Rate      : {sample_stats['initial_success_rate'] * 100:.2f}% ({sample_stats['initial_success_count']:,})")
    print(f"  Initial Failure Rate      : {sample_stats['failure_rate'] * 100:.2f}% ({sample_stats['failed_payment_count']:,})")
    print(f"  Recoverable Failures      : {sample_stats['recoverable_failure_instances']:,}")
    print(f"  Non-Recoverable Failures  : {sample_stats['non_recoverable_failure_instances']:,}")
    print("-" * 50)
    print("  Payment Lifecycle States:")
    for state, count in sample_stats["state_distribution"].items():
        pct = (count / sample_stats["total_payments"]) * 100
        print(f"    - {state:<20}: {count:>6,} ({pct:>5.1f}%)")
    print("-" * 50)
    print("  Payment Methods Distribution:")
    for method, count in sample_stats["method_distribution"].items():
        pct = (count / sample_stats["total_payments"]) * 100
        print(f"    - {method:<20}: {count:>6,} ({pct:>5.1f}%)")
    print("-" * 50)
    print("  Failure Categories:")
    for cat, count in sample_stats["failure_category_distribution"].items():
        print(f"    - {cat:<20}: {count:>6,}")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    main()
