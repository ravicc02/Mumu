from __future__ import annotations


def open_batch_view(
    service,
    batch,
    account_ids,
    *,
    include_ids=False,
    include_account_ids=False,
    full_names=False,
    account_id_resolver=None,
):
    requested = list(dict.fromkeys(account_ids or []))
    matched_ids = {item['account_id'] for item in batch['items']}
    unmatched = [account_id for account_id in requested if account_id not in matched_ids]
    names = {
        account['id']: account['name']
        for account in batch.get('participants', [])
        if account.get('id')
    }
    items = []
    for item in batch['items']:
        view = {
            'name': item['account_name'] or '同学' if full_names else (item['account_name'] or '同')[:1] + '*',
            'course': item['course_name'],
            'title': item['title'],
            'kind': item['kind'],
            'status': item['status'],
            'message': item['message'],
        }
        if include_account_ids:
            view['account_id'] = (
                account_id_resolver(item['account_id'])
                if account_id_resolver else item['account_id']
            )
        if include_ids:
            view['id'] = item['id']
        items.append(view)
    if not batch['items']:
        for account_id in requested:
            name = names.get(account_id)
            if not name:
                try:
                    name = service.database_provider().get_account(account_id)['name']
                except KeyError:
                    name = '同'
            view = {
                'name': name or '同学' if full_names else (name or '同')[:1] + '*',
                'course': '等待扫码匹配',
                'title': '二维码签到',
                'kind': 'qr',
                'status': 'waiting',
                'message': '等待同学扫描教师二维码',
            }
            if include_account_ids:
                view['account_id'] = (
                    account_id_resolver(account_id)
                    if account_id_resolver else account_id
                )
            if include_ids:
                view['id'] = 'participant-' + account_id
            items.append(view)
        return {'items': items, 'status': 'waiting', 'completed': 0, 'total': len(items)}
    for account_id in unmatched:
        name = names.get(account_id)
        if not name:
            try:
                name = service.database_provider().get_account(account_id)['name']
            except KeyError:
                name = '同'
        view = {
            'name': name or '同学' if full_names else (name or '同')[:1] + '*',
            'course': '未匹配到活动',
            'title': '二维码签到',
            'kind': 'qr',
            'status': 'failed',
            'message': '该账号不在当前二维码对应的班级中',
        }
        if include_account_ids:
            view['account_id'] = (
                account_id_resolver(account_id)
                if account_id_resolver else account_id
            )
        if include_ids:
            view['id'] = 'participant-' + account_id
        items.append(view)
    status = (
        'signing' if batch['status'] == 'running' else
        'completed' if batch['status'] == 'completed' else
        'partial' if batch['status'] == 'needs_review' else
        'queued' if batch['status'] == 'awaiting_input' else
        'failed'
    )
    if unmatched and status == 'completed':
        status = 'partial'
    return {
        'items': items,
        'status': status,
        'completed': batch['completed'],
        'total': len(items),
    }
