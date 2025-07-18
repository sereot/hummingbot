#!/usr/bin/env python3
"""
VALR Diagnostic Monitor Script
Monitors bot performance, WebSocket connections, and connector readiness in real-time.
"""

import asyncio
import sys
import time
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

# Add the hummingbot directory to the path
sys.path.insert(0, '/home/mailr/hummingbot-private/hummingbot')

from hummingbot.connector.exchange.valr.valr_exchange import ValrExchange
from unittest.mock import MagicMock


class MockClientConfig:
    def __init__(self):
        self.anonymized_metrics_mode = MagicMock()
        self.anonymized_metrics_mode.get_collector.return_value = MagicMock()
        self.kill_switch_enabled = False
        self.kill_switch_rate = 0
        self.telegram_enabled = False
        self.send_error_logs = False
        self.strategy_report_interval = 900
        self.logger = MagicMock()
        self.instance_id = "diagnostic-monitor"
        self.rate_limits_share_pct = Decimal("100")
        self.commands_timeout = 30
        self.create_command_timeout = 60


class ValrDiagnosticMonitor:
    def __init__(self):
        self.connector = None
        self.start_time = time.time()
        self.status_history = []
        self.last_status_check = 0
        self.monitoring_interval = 5  # Check every 5 seconds
        
    async def initialize_connector(self):
        """Initialize the VALR connector for monitoring."""
        print("🔧 Initializing VALR connector for monitoring...")
        
        mock_config = MockClientConfig()
        
        self.connector = ValrExchange(
            client_config_map=mock_config,
            valr_api_key="test_key",
            valr_api_secret="test_secret",
            trading_pairs=["DOGE-USDT"],
            trading_required=False  # Read-only monitoring
        )
        
        print("✅ VALR connector initialized")
        return True
        
    def get_connector_status(self) -> Dict:
        """Get detailed connector status."""
        if not self.connector:
            return {"error": "No connector initialized"}
            
        status = {
            "timestamp": datetime.now().isoformat(),
            "uptime": time.time() - self.start_time,
            "ready": self.connector.ready,
            "network_status": str(self.connector.network_status),
            "trading_pairs": list(self.connector.trading_pairs),
            "status_dict": self.connector.status_dict
        }
        
        # Add WebSocket status if available
        if hasattr(self.connector, '_user_stream_data_source'):
            ws_source = self.connector._user_stream_data_source
            if hasattr(ws_source, '_websocket_connection_stats'):
                status["websocket_stats"] = ws_source._websocket_connection_stats
                
        return status
        
    def analyze_status_trends(self) -> Dict:
        """Analyze status trends over time."""
        if len(self.status_history) < 2:
            return {"analysis": "Not enough data for trend analysis"}
            
        latest = self.status_history[-1]
        previous = self.status_history[-2]
        
        analysis = {
            "readiness_change": latest.get("ready") != previous.get("ready"),
            "status_changes": [],
            "stability_score": 0
        }
        
        # Compare status_dict changes
        latest_status = latest.get("status_dict", {})
        previous_status = previous.get("status_dict", {})
        
        for key in latest_status:
            if key in previous_status:
                if latest_status[key] != previous_status[key]:
                    analysis["status_changes"].append({
                        "component": key,
                        "from": previous_status[key],
                        "to": latest_status[key]
                    })
        
        # Calculate stability score (0-100)
        ready_count = sum(1 for s in self.status_history[-10:] if s.get("ready", False))
        analysis["stability_score"] = (ready_count / min(len(self.status_history), 10)) * 100
        
        return analysis
        
    def print_status_report(self, status: Dict, analysis: Dict):
        """Print a formatted status report."""
        print(f"\n{'='*60}")
        print(f"VALR DIAGNOSTIC REPORT - {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'='*60}")
        
        print(f"⏱️  Uptime: {status['uptime']:.1f}s")
        print(f"🔗 Network: {status['network_status']}")
        print(f"📊 Overall Ready: {'✅' if status['ready'] else '❌'}")
        print(f"📈 Stability Score: {analysis.get('stability_score', 0):.1f}%")
        
        print(f"\n📋 Component Status:")
        status_dict = status.get("status_dict", {})
        for component, ready in status_dict.items():
            icon = "✅" if ready else "❌"
            print(f"   {icon} {component}: {ready}")
            
        if analysis.get("status_changes"):
            print(f"\n🔄 Recent Changes:")
            for change in analysis["status_changes"]:
                direction = "📈" if change["to"] else "📉"
                print(f"   {direction} {change['component']}: {change['from']} → {change['to']}")
                
        # WebSocket stats if available
        if "websocket_stats" in status:
            ws_stats = status["websocket_stats"]
            print(f"\n🔌 WebSocket Statistics:")
            print(f"   Success Rate: {ws_stats.get('success_rate', 0):.1f}%")
            print(f"   Connections: {ws_stats.get('total_connections', 0)}")
            print(f"   Disconnections: {ws_stats.get('total_disconnections', 0)}")
            
    async def monitor_loop(self):
        """Main monitoring loop."""
        print("🚀 Starting VALR diagnostic monitoring loop...")
        
        while True:
            try:
                # Get current status
                status = self.get_connector_status()
                self.status_history.append(status)
                
                # Keep only last 100 status entries
                if len(self.status_history) > 100:
                    self.status_history = self.status_history[-100:]
                
                # Analyze trends
                analysis = self.analyze_status_trends()
                
                # Print report
                self.print_status_report(status, analysis)
                
                # Wait for next check
                await asyncio.sleep(self.monitoring_interval)
                
            except Exception as e:
                print(f"❌ Error in monitoring loop: {e}")
                await asyncio.sleep(5)
                
    async def run_diagnostic_session(self, duration: int = 300):
        """Run a diagnostic session for specified duration."""
        print(f"🔍 Starting {duration}s diagnostic session...")
        
        # Initialize connector
        await self.initialize_connector()
        
        # Wait for initial setup
        print("⏳ Waiting 10s for connector initialization...")
        await asyncio.sleep(10)
        
        # Run monitoring
        end_time = time.time() + duration
        
        while time.time() < end_time:
            try:
                # Get status
                status = self.get_connector_status()
                self.status_history.append(status)
                
                # Analyze
                analysis = self.analyze_status_trends()
                
                # Print report
                self.print_status_report(status, analysis)
                
                # Wait
                await asyncio.sleep(self.monitoring_interval)
                
            except Exception as e:
                print(f"❌ Error: {e}")
                await asyncio.sleep(5)
                
        print(f"\n🎯 Diagnostic session completed")
        return self.generate_final_report()
        
    def generate_final_report(self) -> Dict:
        """Generate a comprehensive final report."""
        if not self.status_history:
            return {"error": "No data collected"}
            
        report = {
            "session_duration": time.time() - self.start_time,
            "total_checks": len(self.status_history),
            "ready_percentage": 0,
            "component_reliability": {},
            "key_findings": []
        }
        
        # Calculate ready percentage
        ready_count = sum(1 for s in self.status_history if s.get("ready", False))
        report["ready_percentage"] = (ready_count / len(self.status_history)) * 100
        
        # Calculate component reliability
        all_components = set()
        for status in self.status_history:
            all_components.update(status.get("status_dict", {}).keys())
            
        for component in all_components:
            component_ready_count = sum(
                1 for s in self.status_history 
                if s.get("status_dict", {}).get(component, False)
            )
            report["component_reliability"][component] = (
                component_ready_count / len(self.status_history)
            ) * 100
            
        # Generate key findings
        if report["ready_percentage"] < 50:
            report["key_findings"].append("⚠️ Connector readiness is below 50%")
            
        for component, reliability in report["component_reliability"].items():
            if reliability < 80:
                report["key_findings"].append(f"⚠️ {component} has low reliability: {reliability:.1f}%")
                
        return report


async def main():
    """Main function to run the diagnostic monitor."""
    monitor = ValrDiagnosticMonitor()
    
    print("🔍 VALR Diagnostic Monitor")
    print("=" * 40)
    print("1. Run 5-minute diagnostic session")
    print("2. Run continuous monitoring")
    print("3. Run quick 1-minute test")
    
    choice = input("\nSelect option (1-3): ").strip()
    
    if choice == "1":
        final_report = await monitor.run_diagnostic_session(300)  # 5 minutes
    elif choice == "2":
        await monitor.monitor_loop()  # Continuous
    elif choice == "3":
        final_report = await monitor.run_diagnostic_session(60)  # 1 minute
    else:
        print("Invalid choice")
        return
        
    if 'final_report' in locals():
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


if __name__ == "__main__":
    asyncio.run(main())