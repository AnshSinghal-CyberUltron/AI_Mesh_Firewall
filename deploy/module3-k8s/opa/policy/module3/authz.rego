package module3.authz

default allow = false

tenant = object.get(input, "tenant", "")

environment = object.get(input, "environment", "")

path = object.get(input, "path", "/")

estimated_tokens = to_number(object.get(input, "estimated_tokens", 0))

quota = data.module3.quotas[tenant][environment]

path_denied {
	some i
	denied := object.get(quota, "denied_paths", [])[i]
	startswith(path, denied)
}

allow {
	tenant != ""
	environment != ""
	quota.enabled == true
	not path_denied
	quota.used_minute + estimated_tokens <= quota.tpm
	quota.used_day + estimated_tokens <= quota.tpd
}
