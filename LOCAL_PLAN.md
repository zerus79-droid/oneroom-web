# tenants.py 소규모 정리 계획

로컬 `deepseek-r1:8b`가 초안을 씀. **그대로 적용하면 안 됨.**
(환각: `building_id`, SQLAlchemy `TenantForm`, 라우트를 `/tenants`로 바꾸기 — 실제와 다름)

아래가 고친 적용안. Grok/다음 AI는 이것만 따라라.

## 만들 헬퍼 (같은 파일, 동작 동일)

1. `_render_tenant_form(form, buildings, rooms, **extra)`  
   반복되는 `render_template("tenant_form.html", ...)` 한곳으로.
2. `_load_tenant_form_from_args(args)`  
   GET: 번지/호수/순번으로 폼 채우기. 이미 있는 `_lookup_tenant_row` 사용.
3. `_tenant_action_new(form)`  
   action=new: 키만 남기고 빈 폼 + 다음 순번.
4. `_tenant_action_delete(form)`  
   퇴실자 삭제 금지 유지. SQL은 여기 그대로.
5. `_validate_tenant_save(form)`  
   주소·호수·성명·건물/호수 존재·금액·보증금+예치금. 실패 시 메시지 문자열.
6. `_saved_snapshot(form, was_insert)`  
   지금 `tenant_manage` 안 중첩 함수를 모듈 함수로.

## tenant_manage 에 둘 것

- 라우트 `/tenants/manage` (바꾸지 말 것)
- POST에서 action 분기만 (new / delete / save)
- INSERT/UPDATE SQL (검증만 헬퍼로)

## 하지 말 것

- `tenant_form.html`, CSS `.tr-*` / `.tenant-reg-*`
- 새 폴더, Blueprint, SQLAlchemy
- `building_id` 같은 없는 필드
- 라우트 URL 변경

## 적용 순서

1. `_render_tenant_form` 완료 (중복 제거)
2. GET 로드 헬퍼 완료 (`_load_tenant_form_from_args`)
3. new / delete 완료 (`_tenant_action_new`, `_tenant_action_delete`)
4. 저장 검증 + snapshot 완료 (`_validate_tenant_save`, `_parse_tenant_amounts`, `_saved_snapshot`)

파일: `tenants.py` 만.
