import os,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','main_app.settings');django.setup()
from policy.models import Policy
from policy.engine import evaluate

ctx = {'prompt': 'test query for domain isolation check', 'response': ''}
for d in ['rag', 'mcp', 'pipeline', 'global', None]:
    r = evaluate(ctx, domain=d)
    print(f"domain={d}: action={r['action']}, matched={r['matched_count']}")
