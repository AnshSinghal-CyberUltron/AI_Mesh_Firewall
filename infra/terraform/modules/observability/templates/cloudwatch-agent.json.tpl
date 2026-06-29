{
  "agent": {
    "metrics_collection_interval": 60
  },
  "metrics": {
    "namespace": "AIMeshFirewall/EC2",
    "append_dimensions": {
      "InstanceId": "$${aws:InstanceId}"
    },
    "metrics_collected": {
      "cpu": {
        "measurement": ["cpu_usage_idle", "cpu_usage_system", "cpu_usage_user"],
        "metrics_collection_interval": 60,
        "totalcpu": true
      },
      "disk": {
        "measurement": ["used_percent"],
        "metrics_collection_interval": 60,
        "resources": ["/", "/var/lib/docker"]
      },
      "mem": {
        "measurement": ["mem_used_percent"],
        "metrics_collection_interval": 60
      }
    }
  },
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/messages",
            "log_group_name": "/ai-mesh-firewall/ec2/host",
            "log_stream_name": "{instance_id}/messages"
          }
        ]
      }
    }
  }
}
