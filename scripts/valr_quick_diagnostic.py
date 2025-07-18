#!/usr/bin/env python3
"""
Quick VALR Diagnostic Script
Runs a quick 1-minute diagnostic test automatically.
"""

import asyncio
import sys
import time
from datetime import datetime

# Add the hummingbot directory to the path
sys.path.insert(0, '/home/mailr/hummingbot-private/hummingbot')

from valr_diagnostic_monitor import ValrDiagnosticMonitor


async def main():
    """Run a quick 1-minute diagnostic test."""
    print("🔍 VALR Quick Diagnostic - Running 1-minute test...")
    
    monitor = ValrDiagnosticMonitor()
    
    # Run 1-minute diagnostic session
    final_report = await monitor.run_diagnostic_session(60)
    
    # Print final report
    print("\n" + "="*60)
    print("FINAL DIAGNOSTIC REPORT")
    print("="*60)
    print(f"Session Duration: {final_report['session_duration']:.1f}s")
    print(f"Total Checks: {final_report['total_checks']}")
    print(f"Ready Percentage: {final_report['ready_percentage']:.1f}%")
    print(f"\nComponent Reliability:")
    for component, reliability in final_report['component_reliability'].items():
        print(f"  {component}: {reliability:.1f}%")
    print(f"\nKey Findings:")
    for finding in final_report['key_findings']:
        print(f"  {finding}")
    
    # Provide recommendations
    print(f"\n🎯 RECOMMENDATIONS:")
    if final_report['ready_percentage'] >= 80:
        print("  ✅ Connector is performing well")
    elif final_report['ready_percentage'] >= 50:
        print("  ⚠️ Connector has intermittent issues - review component failures")
    else:
        print("  ❌ Connector has significant issues - investigate initialization problems")
    
    # Check for specific component issues
    problematic_components = [
        comp for comp, reliability in final_report['component_reliability'].items()
        if reliability < 80
    ]
    
    if problematic_components:
        print(f"  🔧 Focus on these components: {', '.join(problematic_components)}")
    
    return final_report


if __name__ == "__main__":
    asyncio.run(main())