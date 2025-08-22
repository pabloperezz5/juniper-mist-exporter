# Mist API to Mimir Exporter

A Python-based exporter that collects metrics from Juniper Mist Cloud API and sends them to Grafana Mimir for monitoring and visualization.

## Overview

This exporter extracts comprehensive networking metrics from Juniper Mist infrastructure including:
- Access Points (APs)
- Switches
- Gateways
- Connected clients
- Environmental sensors

The collected metrics are formatted as Prometheus metrics and sent directly to Mimir.

## Installation

1. Clone or download the exporter:
```bash
git clone <repository-url>
cd mist-exporter
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure environment variables (see Configuration section)

## Configuration

### Environment Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `MIST_API_TOKEN` | Mist API authentication token | Yes | `token_here` |
| `MIST_ORG_ID` | Mist organization UUID | Yes | `org_id_here` |
| `MIST_BASE_URL` | Mist base URL | Yes | `https://api.mist.com/api/v1` |
| `MIMIR_ENDPOINT` | Mimir remote write endpoint | Yes | `https://mimir.example-domain.com/api/v1/push` |
| `MIMIR_ORG_ID` | Mimir tenant/organization ID | No | `mimir_org_id_here` |

## Usage

### Test Connectivity
Verify API access and infrastructure connectivity:
```bash
python3 mist_exporter.py --test
```

### View Infrastructure Summary
Get an overview of your Mist environment:
```bash
python3 mist_exporter.py --summary
```

### Single Collection Run
Execute one collection cycle:
```bash
python3 mist_exporter.py --once
```

### Continuous Monitoring
Run with custom interval (default: 60 seconds):
```bash
python3 mist_exporter.py --interval 300
```

### Verbose Logging
Enable detailed debug output:
```bash
python3 mist_exporter.py --verbose
```

## Metrics Reference

### Device Status Metrics
- `mist_device_status` - Device connectivity (1=connected, 0=disconnected)
- `mist_device_uptime_seconds` - Device uptime
- `mist_device_last_seen_timestamp` - Last communication timestamp

### Performance Metrics
- `mist_device_cpu_usage_percent` - CPU utilization
- `mist_device_memory_usage_percent` - Memory utilization
- `mist_device_memory_used_kb` - Used memory in KB
- `mist_device_memory_total_kb` - Total memory in KB

### Network Traffic
- `mist_device_tx_bytes_total` - Transmitted bytes counter
- `mist_device_rx_bytes_total` - Received bytes counter
- `mist_device_tx_packets_total` - Transmitted packets counter
- `mist_device_rx_packets_total` - Received packets counter
- `mist_device_tx_bps` - Transmission rate (bits/sec)
- `mist_device_rx_bps` - Reception rate (bits/sec)

### WiFi-Specific Metrics
- `mist_device_client_count` - Connected wireless clients
- `mist_ap_radio_utilization_percent` - WiFi radio utilization
- `mist_ap_radio_power_dbm` - Radio transmission power
- `mist_ap_radio_channel` - Operating channel
- `mist_ap_radio_noise_floor_dbm` - Background noise level

### Environmental Metrics
- `mist_device_cpu_temperature_celsius` - CPU temperature
- `mist_device_ambient_temperature_celsius` - Ambient temperature
- `mist_device_humidity_percent` - Relative humidity

### Port Metrics (Switches)
- `mist_device_port_status` - Port status (1=up, 0=down)
- `mist_device_port_tx_bytes_total` - Port transmitted bytes
- `mist_device_port_rx_bytes_total` - Port received bytes

### Power Metrics
- `mist_device_power_draw_watts` - Power consumption

### Informational
- `mist_device_info` - Static device information
- `mist_device_version` - Firmware version

## Metric Labels

All metrics include comprehensive labels for filtering and grouping:

### Common Labels
- `device_id` - Unique device identifier
- `device_name` - Device hostname/name
- `site_id` - Site identifier
- `site_name` - Site name
- `mac` - Device MAC address
- `model` - Device model
- `type` - Device type (ap, switch, gateway)
- `job` - Exporter job name