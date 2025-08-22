#!/usr/bin/env python3
"""
Mist API to Mimir Exporter
Extracts metrics from Juniper Mist, parses, formats, and sends them to Mimir/Prometheus
"""

import requests
import time
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple
import os
from prometheus_client import CollectorRegistry, Gauge, Counter, Info
from prometheus_client.openmetrics.exposition import generate_latest as generate_openmetrics
import snappy
import struct

# Configuration
MIST_API_TOKEN = os.getenv('MIST_API_TOKEN', 'token_here')
MIST_ORG_ID = os.getenv('MIST_ORG_ID', 'org_id_here')
MIST_BASE_URL = os.getenv('MIST_BASE_URL', 'https://api.mist.com/api/v1')

# Mimir configuration
MIMIR_URL = os.getenv('MIMIR_URL', 'https://mimir.example-domain.com/api/v1/push')
MIMIR_ORG_ID = os.getenv('MIMIR_ORG_ID', 'mimir_org_id_here')
JOB_NAME = 'juniper-mist-exporter'

# Logging configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def encode_varint(value: int) -> bytes:
        """Encode a varint (variable-length integer) for protobuf"""
        result = bytearray()
        while value >= 0x80:
            result.append((value & 0x7F) | 0x80)
            value >>= 7
        result.append(value & 0x7F)
        return bytes(result)

def encode_string(s: str) -> bytes:
    """Encode a string for protobuf"""
    encoded = s.encode('utf-8')
    return encode_varint(len(encoded)) + encoded

def encode_double(value: float) -> bytes:
    """Encode a double for protobuf (wire type 1, fixed64)"""
    return struct.pack('<d', value)

def encode_int64(value: int) -> bytes:
    """Encode an int64 as varint for protobuf"""
    return encode_varint(value)

def encode_label(name: str, value: str) -> bytes:
    """Encode a Label message
    message Label {
    string name = 1;   // tag 1, wire type 2 (length-delimited)
    string value = 2;  // tag 2, wire type 2 (length-delimited)
    }
    """
    data = b''
    # name field (tag 1, wire type 2)
    data += b'\x0a' + encode_string(name)
    # value field (tag 2, wire type 2)
    data += b'\x12' + encode_string(value)
    return data

def encode_sample(timestamp_ms: int, value: float) -> bytes:
    """Encode a Sample message
    message Sample {
    double value = 1;     // tag 1, wire type 1 (fixed64)
    int64 timestamp = 2;  // tag 2, wire type 0 (varint)
    }
    """
    data = b''
    # value field (tag 1, wire type 1 - fixed64)
    data += b'\x09' + encode_double(value)
    # timestamp field (tag 2, wire type 0 - varint)
    data += b'\x10' + encode_int64(timestamp_ms)
    return data

def encode_timeseries(labels: Dict[str, str], samples: List[Tuple[int, float]]) -> bytes:
    """Encode a TimeSeries message
    message TimeSeries {
    repeated Label labels = 1;   // tag 1, wire type 2 (length-delimited)
    repeated Sample samples = 2; // tag 2, wire type 2 (length-delimited)
    }
    """
    data = b''

    # labels field (tag 1, repeated)
    for name, value in labels.items():
        label_data = encode_label(name, value)
        data += b'\x0a' + encode_varint(len(label_data)) + label_data

    # samples field (tag 2, repeated)
    for timestamp, val in samples:
        sample_data = encode_sample(timestamp, val)
        data += b'\x12' + encode_varint(len(sample_data)) + sample_data

    return data

def encode_write_request(timeseries_list: List[bytes]) -> bytes:
    """Encode a WriteRequest message
    message WriteRequest {
    repeated TimeSeries timeseries = 1; // tag 1, wire type 2 (length-delimited)
    }
    """
    data = b''

    # timeseries field (tag 1, repeated)
    for ts_data in timeseries_list:
        data += b'\x0a' + encode_varint(len(ts_data)) + ts_data

    return data

class MistAPIClient:
    def __init__(self, token: str, org_id: str):
        self.token = token
        self.org_id = org_id
        self.base_url = MIST_BASE_URL
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Token {self.token}',
            'Content-Type': 'application/json'
        })

    def get_organization_info(self) -> Dict:
        """Gets organization information"""
        url = f"{self.base_url}/orgs/{self.org_id}"
        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Error getting organization info: {e}")
            return {}

    def get_sites(self) -> List[Dict]:
        """Gets list of sites"""
        url = f"{self.base_url}/orgs/{self.org_id}/sites"
        try:
            response = self.session.get(url)
            response.raise_for_status()
            sites = response.json()
            return sites
        except requests.RequestException as e:
            logger.error(f"Error getting sites: {e}")
            return []

    def get_all_devices(self) -> List[Dict]:
        """Gets all devices from the organization"""
        url = f"{self.base_url}/orgs/{self.org_id}/devices"
        try:
            response = self.session.get(url)
            response.raise_for_status()
            data = response.json()
            # Response comes in format {"results": [...]}
            return data.get('results', [])
        except requests.RequestException as e:
            logger.error(f"Error getting devices: {e}")
            return []

    def get_site_device_stats(self, site_id: str) -> List[Dict]:
        """Gets statistics for all devices in a site"""
        url = f"{self.base_url}/sites/{site_id}/stats/devices"
        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Error getting device stats for site {site_id}: {e}")
            return []

    def get_device_stats(self, site_id: str, device_id: str) -> Dict:
        """Gets detailed statistics for a specific device"""
        url = f"{self.base_url}/sites/{site_id}/stats/devices/{device_id}"
        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Error getting stats for device {device_id}: {e}")
            return {}

    def get_site_client_stats(self, site_id: str) -> List[Dict]:
        """Gets statistics for connected clients"""
        url = f"{self.base_url}/sites/{site_id}/stats/clients"
        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Error getting client stats: {e}")
            return []

class PrometheusExporter:
    def __init__(self):
        self.registry = CollectorRegistry()
        self.setup_metrics()

    def setup_metrics(self):
        """Sets up Prometheus metrics based on Mist structure"""

        # Device information (static labels)
        self.device_info = Info(
            'mist_device_info',
            'Mist device information',
            registry=self.registry
        )

        # Device status
        self.device_status = Gauge(
            'mist_device_status',
            'Device status (1=connected, 0=disconnected)',
            ['device_id', 'device_name', 'site_id', 'site_name', 'org_id', 'mac', 'serial', 'model', 'type', 'hw_rev', 'deviceprofile_name'],
            registry=self.registry
        )

        # Connected clients
        self.device_client_count = Gauge(
            'mist_device_client_count',
            'Number of clients connected to the device',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'model', 'type'],
            registry=self.registry
        )

        # CPU usage
        self.device_cpu_usage = Gauge(
            'mist_device_cpu_usage_percent',
            'Device CPU usage percentage',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'model', 'type'],
            registry=self.registry
        )

        # Memory usage
        self.device_memory_usage = Gauge(
            'mist_device_memory_usage_percent',
            'Device memory usage percentage',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'model', 'type'],
            registry=self.registry
        )

        self.device_memory_used_kb = Gauge(
            'mist_device_memory_used_kb',
            'Memory used in KB',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'model', 'type'],
            registry=self.registry
        )

        self.device_memory_total_kb = Gauge(
            'mist_device_memory_total_kb',
            'Total memory in KB',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'model', 'type'],
            registry=self.registry
        )

        # Uptime
        self.device_uptime = Gauge(
            'mist_device_uptime_seconds',
            'Device uptime in seconds',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'model', 'type'],
            registry=self.registry
        )

        # Network traffic
        self.device_tx_bytes = Counter(
            'mist_device_tx_bytes_total',
            'Bytes transmitted by the device',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'model', 'type'],
            registry=self.registry
        )

        self.device_rx_bytes = Counter(
            'mist_device_rx_bytes_total',
            'Bytes received by the device',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'model', 'type'],
            registry=self.registry
        )

        self.device_tx_pkts = Counter(
            'mist_device_tx_packets_total',
            'Packets transmitted by the device',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'model', 'type'],
            registry=self.registry
        )

        self.device_rx_pkts = Counter(
            'mist_device_rx_packets_total',
            'Packets received by the device',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'model', 'type'],
            registry=self.registry
        )

        # Network speed (BPS)
        self.device_tx_bps = Gauge(
            'mist_device_tx_bps',
            'Transmission speed in bits per second',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'model', 'type'],
            registry=self.registry
        )

        self.device_rx_bps = Gauge(
            'mist_device_rx_bps',
            'Reception speed in bits per second',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'model', 'type'],
            registry=self.registry
        )

        # WiFi specific metrics (Access Points)
        self.ap_radio_utilization = Gauge(
            'mist_ap_radio_utilization_percent',
            'WiFi radio utilization',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'band', 'channel'],
            registry=self.registry
        )

        self.ap_radio_power = Gauge(
            'mist_ap_radio_power_dbm',
            'WiFi radio power in dBm',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'band', 'channel'],
            registry=self.registry
        )

        self.ap_radio_channel = Gauge(
            'mist_ap_radio_channel',
            'WiFi radio channel',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'band'],
            registry=self.registry
        )

        self.ap_radio_noise_floor = Gauge(
            'mist_ap_radio_noise_floor_dbm',
            'WiFi radio noise floor in dBm',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'band'],
            registry=self.registry
        )

        # Temperature and environmental metrics
        self.device_cpu_temp = Gauge(
            'mist_device_cpu_temperature_celsius',
            'CPU temperature in Celsius',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'model'],
            registry=self.registry
        )

        self.device_ambient_temp = Gauge(
            'mist_device_ambient_temperature_celsius',
            'Ambient temperature in Celsius',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'model'],
            registry=self.registry
        )

        self.device_humidity = Gauge(
            'mist_device_humidity_percent',
            'Relative humidity percentage',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'model'],
            registry=self.registry
        )

        # Port metrics (for switches and APs)
        self.port_status = Gauge(
            'mist_device_port_status',
            'Port status (1=up, 0=down)',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'port', 'speed'],
            registry=self.registry
        )

        self.port_tx_bytes = Counter(
            'mist_device_port_tx_bytes_total',
            'Bytes transmitted per port',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'port'],
            registry=self.registry
        )

        self.port_rx_bytes = Counter(
            'mist_device_port_rx_bytes_total',
            'Bytes received per port',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'port'],
            registry=self.registry
        )

        # Power metrics
        self.device_power_draw = Gauge(
            'mist_device_power_draw_watts',
            'Power consumption in watts',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'power_src'],
            registry=self.registry
        )

        # Total WLANs count
        self.device_wlan_count = Gauge(
            'mist_device_wlan_count',
            'Number of configured WLANs',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac'],
            registry=self.registry
        )

        # Last seen timestamp
        self.device_last_seen = Gauge(
            'mist_device_last_seen_timestamp',
            'Timestamp when device was last seen',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac'],
            registry=self.registry
        )

        # Firmware version
        self.device_version_info = Info(
            'mist_device_version',
            'Device firmware version',
            ['device_id', 'device_name', 'site_id', 'site_name', 'mac'],
            registry=self.registry
        )

    def get_device_labels(self, device_info: Dict, site_info: Dict, stats: Dict) -> Dict[str, str]:
        """Extracts common device labels"""
        return {
            'device_id': device_info.get('id', stats.get('id', '')),
            'device_name': device_info.get('name', stats.get('name', 'Unknown')),
            'site_id': site_info.get('id', ''),
            'site_name': site_info.get('name', 'Unknown'),
            'org_id': device_info.get('org_id', ''),
            'mac': device_info.get('mac', stats.get('mac', '')),
            'serial': device_info.get('serial', stats.get('serial', '')),
            'model': device_info.get('model', stats.get('model', '')),
            'type': device_info.get('type', stats.get('type', '')),
            'hw_rev': device_info.get('hw_rev', stats.get('hw_rev', '')),
            'deviceprofile_name': device_info.get('deviceprofile_name', stats.get('deviceprofile_name', ''))
        }

    def update_device_metrics(self, device_info: Dict, site_info: Dict, stats: Dict):
        """Updates metrics for a device"""
        if not stats:
            return

        # Base labels
        base_labels = self.get_device_labels(device_info, site_info, stats)

        # Common labels
        common_labels = {k: v for k, v in base_labels.items() if k in ['device_id', 'device_name', 'site_id', 'site_name', 'mac', 'model', 'type']}

        # Basic labels
        basic_labels = {k: v for k, v in base_labels.items() if k in ['device_id', 'device_name', 'site_id', 'site_name', 'mac']}

        # Device status
        status_value = 1 if stats.get('status') == 'connected' else 0
        self.device_status.labels(**base_labels).set(status_value)

        # Connected clients
        num_clients = stats.get('num_clients', 0)
        if num_clients is not None:
            self.device_client_count.labels(**common_labels).set(num_clients)

        # CPU and Memory
        cpu_util = stats.get('cpu_util')
        if cpu_util is not None:
            self.device_cpu_usage.labels(**common_labels).set(cpu_util)

        mem_used_kb = stats.get('mem_used_kb')
        mem_total_kb = stats.get('mem_total_kb')
        if mem_used_kb is not None and mem_total_kb is not None and mem_total_kb > 0:
            memory_percent = (mem_used_kb / mem_total_kb) * 100
            self.device_memory_usage.labels(**common_labels).set(memory_percent)
            self.device_memory_used_kb.labels(**common_labels).set(mem_used_kb)
            self.device_memory_total_kb.labels(**common_labels).set(mem_total_kb)

        # Uptime
        uptime = stats.get('uptime')
        if uptime is not None:
            self.device_uptime.labels(**common_labels).set(uptime)

        # Network traffic
        tx_bytes = stats.get('tx_bytes')
        rx_bytes = stats.get('rx_bytes')
        tx_pkts = stats.get('tx_pkts')
        rx_pkts = stats.get('rx_pkts')
        tx_bps = stats.get('tx_bps')
        rx_bps = stats.get('rx_bps')

        if tx_bytes is not None:
            self.device_tx_bytes.labels(**common_labels)._value._value = tx_bytes
        if rx_bytes is not None:
            self.device_rx_bytes.labels(**common_labels)._value._value = rx_bytes
        if tx_pkts is not None:
            self.device_tx_pkts.labels(**common_labels)._value._value = tx_pkts
        if rx_pkts is not None:
            self.device_rx_pkts.labels(**common_labels)._value._value = rx_pkts
        if tx_bps is not None:
            self.device_tx_bps.labels(**common_labels).set(tx_bps)
        if rx_bps is not None:
            self.device_rx_bps.labels(**common_labels).set(rx_bps)

        # Last seen timestamp
        last_seen = stats.get('last_seen')
        if last_seen is not None:
            self.device_last_seen.labels(**basic_labels).set(last_seen)

        # Firmware version
        version = stats.get('version')
        if version:
            self.device_version_info.labels(**basic_labels).info({'version': version})

        # WLANs
        num_wlans = stats.get('num_wlans')
        if num_wlans is not None:
            self.device_wlan_count.labels(**basic_labels).set(num_wlans)

        # Access Point specific metrics
        if stats.get('type') == 'ap':
            self._update_ap_metrics(device_info, site_info, stats)

        # Port metrics
        self._update_port_metrics(device_info, site_info, stats)

        # Environmental metrics
        self._update_environmental_metrics(device_info, site_info, stats)

        # Power metrics
        self._update_power_metrics(device_info, site_info, stats)

    def _update_ap_metrics(self, device_info: Dict, site_info: Dict, stats: Dict):
        """Updates Access Point specific metrics"""
        radio_stats = stats.get('radio_stat', {})

        for band, radio_data in radio_stats.items():
            if not isinstance(radio_data, dict):
                continue

            radio_labels = {
                'device_id': device_info.get('id', stats.get('id', '')),
                'device_name': device_info.get('name', stats.get('name', 'Unknown')),
                'site_id': site_info.get('id', ''),
                'site_name': site_info.get('name', 'Unknown'),
                'mac': device_info.get('mac', stats.get('mac', '')),
                'band': band,
                'channel': str(radio_data.get('channel', ''))
            }

            channel_labels = {k: v for k, v in radio_labels.items() if k != 'channel'}

            # Radio utilization
            util_all = radio_data.get('util_all')
            if util_all is not None:
                self.ap_radio_utilization.labels(**radio_labels).set(util_all)

            # Power
            power = radio_data.get('power')
            if power is not None:
                self.ap_radio_power.labels(**radio_labels).set(power)

            # Channel
            channel = radio_data.get('channel')
            if channel is not None:
                self.ap_radio_channel.labels(**channel_labels).set(channel)

            # Noise floor
            noise_floor = radio_data.get('noise_floor')
            if noise_floor is not None:
                self.ap_radio_noise_floor.labels(**channel_labels).set(noise_floor)

    def _update_port_metrics(self, device_info: Dict, site_info: Dict, stats: Dict):
        """Updates port metrics"""
        port_stats = stats.get('port_stat', {})

        for port_name, port_data in port_stats.items():
            if not isinstance(port_data, dict):
                continue

            port_labels = {
                'device_id': device_info.get('id', stats.get('id', '')),
                'device_name': device_info.get('name', stats.get('name', 'Unknown')),
                'site_id': site_info.get('id', ''),
                'site_name': site_info.get('name', 'Unknown'),
                'mac': device_info.get('mac', stats.get('mac', '')),
                'port': port_name,
                'speed': str(port_data.get('speed', ''))
            }

            # Port status
            port_up = 1 if port_data.get('up') else 0
            self.port_status.labels(**port_labels).set(port_up)

            # Port traffic
            port_tx_bytes = port_data.get('tx_bytes')
            port_rx_bytes = port_data.get('rx_bytes')

            traffic_labels = {k: v for k, v in port_labels.items() if k != 'speed'}

            if port_tx_bytes is not None:
                self.port_tx_bytes.labels(**traffic_labels)._value._value = port_tx_bytes
            if port_rx_bytes is not None:
                self.port_rx_bytes.labels(**traffic_labels)._value._value = port_rx_bytes

    def _update_environmental_metrics(self, device_info: Dict, site_info: Dict, stats: Dict):
        """Updates environmental metrics"""
        env_stats = stats.get('env_stat', {})

        if not env_stats:
            return

        env_labels = {
            'device_id': device_info.get('id', stats.get('id', '')),
            'device_name': device_info.get('name', stats.get('name', 'Unknown')),
            'site_id': site_info.get('id', ''),
            'site_name': site_info.get('name', 'Unknown'),
            'mac': device_info.get('mac', stats.get('mac', '')),
            'model': device_info.get('model', stats.get('model', ''))
        }

        cpu_temp = env_stats.get('cpu_temp')
        if cpu_temp is not None:
            self.device_cpu_temp.labels(**env_labels).set(cpu_temp)

        ambient_temp = env_stats.get('ambient_temp')
        if ambient_temp is not None:
            self.device_ambient_temp.labels(**env_labels).set(ambient_temp)

        humidity = env_stats.get('humidity')
        if humidity is not None:
            self.device_humidity.labels(**env_labels).set(humidity)

    def _update_power_metrics(self, device_info: Dict, site_info: Dict, stats: Dict):
        """Updates power metrics"""
        # Power from LLDP
        lldp_stats = stats.get('lldp_stats', {})
        if isinstance(lldp_stats, dict):
            for port, lldp_data in lldp_stats.items():
                if isinstance(lldp_data, dict):
                    power_draw = lldp_data.get('power_draw')
                    if power_draw is not None:
                        power_labels = {
                            'device_id': device_info.get('id', stats.get('id', '')),
                            'device_name': device_info.get('name', stats.get('name', 'Unknown')),
                            'site_id': site_info.get('id', ''),
                            'site_name': site_info.get('name', 'Unknown'),
                            'mac': device_info.get('mac', stats.get('mac', '')),
                            'power_src': stats.get('power_src', 'Unknown')
                        }
                        # Convert from mW to W
                        power_watts = power_draw / 1000 if power_draw else 0
                        self.device_power_draw.labels(**power_labels).set(power_watts)

    def push_metrics(self):
        """Sends metrics to Mimir using remote_write protocol"""
        try:
            timeseries_list = []
            current_time_ms = int(time.time() * 1000)

            # Collect metrics and convert to protobuf format
            for metric in self.registry.collect():
                for sample in metric.samples:
                    # Create labels dict
                    labels = {'__name__': sample.name}

                    # Add other labels
                    if sample.labels:
                        for label_name, label_value in sample.labels.items():
                            labels[label_name] = str(label_value)

                    # Create samples list - [(timestamp_ms, value)]
                    samples = [(current_time_ms, float(sample.value))]

                    # Encode this timeseries
                    ts_data = encode_timeseries(labels, samples)
                    timeseries_list.append(ts_data)

            if not timeseries_list:
                logger.warning("No metrics to send to Mimir")
                return

            # Encode WriteRequest
            write_request_data = encode_write_request(timeseries_list)

            # Compress with Snappy
            compressed_data = snappy.compress(write_request_data)

            # Send to Mimir
            headers = {
                'Content-Type': 'application/x-protobuf',
                'Content-Encoding': 'snappy',
                'X-Prometheus-Remote-Write-Version': '0.1.0',
                'X-Scope-OrgID': MIMIR_ORG_ID,
            }

            response = requests.post(
                MIMIR_URL,
                data=compressed_data,
                headers=headers,
                timeout=30
            )

            if response.status_code >= 400:
                logger.error(f"Error sending metrics to Mimir: {response.status_code} - {response.text}")
            else:
                logger.info(f"Metrics successfully sent to Mimir: {response.status_code}")

        except Exception as e:
            logger.error(f"Error sending metrics to Mimir: {e}")

class MistExporter:
    def __init__(self):
        self.mist_client = MistAPIClient(MIST_API_TOKEN, MIST_ORG_ID)
        self.prometheus_exporter = PrometheusExporter()

        # Maps for device mapping (rebuilt on each run)
        self._sites_map = {}
        self._devices_map = {}

    def _build_maps(self):
        """Builds fresh site and device maps for quick reference"""
        # Get fresh sites data
        sites = self.mist_client.get_sites()
        self._sites_map = {site['id']: site for site in sites}

        # Get fresh devices data
        devices = self.mist_client.get_all_devices()
        self._devices_map = {device['mac']: device for device in devices}

        logger.info(f"Built fresh maps: {len(self._sites_map)} sites and {len(self._devices_map)} devices")

    def run_once(self):
        """Executes a complete collection iteration"""
        logger.info("Starting Mist metrics collection...")

        # Build fresh reference maps on each run
        self._build_maps()

        # Get fresh organization information
        org_info = self.mist_client.get_organization_info()
        org_name = org_info.get('name', 'Unknown')

        logger.info(f"Processing organization: {org_name}")

        device_count = 0
        connected_devices = 0
        total_clients = 0

        # Process each site with fresh data
        for site_id, site_info in self._sites_map.items():
            site_name = site_info.get('name', 'Unknown')
            logger.info(f"Processing site: {site_name} ({site_id})")

            # Get fresh device statistics for the site
            site_device_stats = self.mist_client.get_site_device_stats(site_id)

            for device_stats in site_device_stats:
                device_count += 1
                device_mac = device_stats.get('mac', '')
                device_name = device_stats.get('name', 'Unknown')

                # Find device information in the map
                device_info = self._devices_map.get(device_mac, {})

                # If we don't have device info, use stats
                if not device_info:
                    device_info = device_stats

                # Update metrics with fresh data
                self.prometheus_exporter.update_device_metrics(device_info, site_info, device_stats)

                # Count statistics
                if device_stats.get('status') == 'connected':
                    connected_devices += 1

                num_clients = device_stats.get('num_clients', 0)
                if num_clients:
                    total_clients += num_clients

                # Basic processing log
                status = device_stats.get('status', 'unknown')
                device_type = device_stats.get('type', device_info.get('type', 'unknown'))

                logger.debug(f"Processed device: {device_name} ({device_type}) - Status: {status}, Clients: {num_clients}")

        # Send metrics to Mimir
        self.prometheus_exporter.push_metrics()

        # Summary log
        summary_message = f"Collection completed: {device_count} devices, {connected_devices} connected, {total_clients} total clients"
        logger.info(summary_message)

    def run_continuous(self, interval: int = 60):
        """Runs the exporter continuously"""
        logger.info(f"Starting continuous exporter with {interval} second interval")
        logger.info(f"Organization: {MIST_ORG_ID}")
        logger.info(f"Mimir Gateway: {MIMIR_URL}")

        while True:
            try:
                start_time = time.time()
                self.run_once()
                execution_time = time.time() - start_time
                logger.info(f"Collection completed in {execution_time:.2f} seconds")

            except Exception as e:
                error_message = f"Error during collection: {e}"
                logger.error(error_message)

            try:
                sleep_time = interval - execution_time
                time.sleep(sleep_time)
            except:
                sleep_time = 0
            logger.info(f"Continuing with the next collection...")


    def test_connectivity(self):
        """Tests connectivity with APIs"""
        logger.info("Testing connectivity...")

        # Test Mist API
        try:
            org_info = self.mist_client.get_organization_info()
            if org_info:
                logger.info(f"Mist API: Connected to organization '{org_info.get('name', 'Unknown')}'")
            else:
                logger.error("Mist API: Error getting organization information")
                return False
        except Exception as e:
            logger.error(f"Mist API: Connectivity error - {e}")
            return False

        # Test sites
        try:
            sites = self.mist_client.get_sites()
            logger.info(f"Mist API: {len(sites)} sites found")
            for site in sites[:5]:  # Show first 5 sites
                logger.info(f"  - {site.get('name', 'Unknown')} ({site.get('id', '')})")
            if len(sites) > 5:
                logger.info(f"  ... and {len(sites) - 5} more sites")
        except Exception as e:
            logger.error(f"Mist API: Error getting sites - {e}")
            return False

        # Test devices
        try:
            devices = self.mist_client.get_all_devices()
            logger.info(f"Mist API: {len(devices)} devices found")

            # Show summary by type
            device_types = {}
            for device in devices:
                device_type = device.get('type', 'unknown')
                device_types[device_type] = device_types.get(device_type, 0) + 1

            for device_type, count in device_types.items():
                logger.info(f"  - {device_type}: {count} devices")

        except Exception as e:
            logger.error(f"Mist API: Error getting devices - {e}")
            return False

        # Test sample metrics with fresh data
        try:
            if sites:
                sample_site = sites[0]
                site_stats = self.mist_client.get_site_device_stats(sample_site['id'])
                logger.info(f"Mist API: Got fresh statistics for {len(site_stats)} devices from site {sample_site.get('name', 'Unknown')}")
        except Exception as e:
            logger.warning(f"Mist API: Error getting sample statistics - {e}")

        return True

    def show_summary(self):
        """Shows infrastructure summary with fresh data"""
        logger.info("Analyzing Mist infrastructure...")

        # Get fresh basic information
        org_info = self.mist_client.get_organization_info()
        sites = self.mist_client.get_sites()
        devices = self.mist_client.get_all_devices()

        print(f"\n{'='*60}")
        print(f"INFRASTRUCTURE SUMMARY")
        print(f"{'='*60}")

        print(f"\n📋 ORGANIZATION:")
        print(f"  Name: {org_info.get('name', 'Unknown')}")
        print(f"  ID: {org_info.get('id', 'Unknown')}")
        print(f"  MSP: {org_info.get('msp_name', 'Unknown')}")

        print(f"\n🏢 SITES ({len(sites)} total):")
        for site in sites:
            print(f"  - {site.get('name', 'Unknown')} ({site.get('address', 'No address')})")

        print(f"\n📊 AVAILABLE METRICS:")
        print("  - Device status (connected/disconnected)")
        print("  - CPU and memory per device")
        print("  - Connected WiFi clients (APs)")
        print("  - Temperatures (CPU and ambient)")
        print("  - Network traffic (TX/RX)")
        print("  - WiFi radio utilization")
        print("  - Port status")
        print("  - Power consumption")
        print("  - Uptime and firmware versions")

        print(f"\n🎯 CURRENT CONFIGURATION:")
        print(f"  Mimir Gateway: {MIMIR_URL}")
        print(f"  Collection interval: 1 minute")
        print(f"  Job name: {JOB_NAME}")

        print(f"\n{'='*60}")

def main():
    import argparse

    parser = argparse.ArgumentParser(description='Mist to Mimir Exporter')
    parser.add_argument('--once', action='store_true', help='Run once')
    parser.add_argument('--test', action='store_true', help='Test connectivity and exit')
    parser.add_argument('--summary', action='store_true', help='Show infrastructure summary and exit')
    parser.add_argument('--interval', type=int, default=60, help='Interval in seconds (default: 300)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Detailed logging')

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Verify configuration
    if not MIST_API_TOKEN:
        logger.error("Please configure MIST_API_TOKEN")
        return 1

    if not MIST_ORG_ID:
        logger.error("Please configure MIST_ORG_ID")
        return 1

    exporter = MistExporter()

    if args.test:
        success = exporter.test_connectivity()
        return 0 if success else 1

    if args.summary:
        exporter.show_summary()
        return 0

    if args.once:
        try:
            exporter.run_once()
            return 0
        except Exception as e:
            logger.error(f"Error in single execution: {e}")
            return 1
    else:
        try:
            exporter.run_continuous(args.interval)
        except KeyboardInterrupt:
            logger.info("Stopping exporter...")
            return 0
        except Exception as e:
            logger.error(f"Error in continuous execution: {e}")
            return 1

if __name__ == '__main__':
    exit(main())