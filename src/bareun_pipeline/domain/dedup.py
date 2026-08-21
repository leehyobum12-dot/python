"""
dedup_key 생성 모듈 (salted SHA-256)
======================================
- 전화번호 평문 노출 방지 (개인정보보호법 준수)
- 동일한 식당의 중복 삽입 방지
- 결정론적 해시 (같은 입력 → 같은 출력)

기존 참고:
- pipeline.py의 make_dedup_key() 함수
- 기존 로직의 의도 보존: 상호명 + 주소 + 전화번호/상세주소 기반 키 생성

보안 개선:
- 기존: 전화번호 평문을 dedup_key에 포함
- 개선: 전화번호를 salted SHA-256으로 해시화
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Optional


def _get_salt() -> str:
    """환경변수에서 salt 로드"""
    return os.environ.get('DEDUP_SALT', 'bareun_default_salt_change_me')


def normalize_name(name: str) -> str:
    """
    상호명 정규화

    기존 pipeline.py의 norm_name() 함수 보존
    - 특수문자 제거
    - 소문자 변환
    """
    if not name:
        return ''
    return re.sub(r'[^가-힣a-zA-Z0-9]', '', str(name)).lower()


def normalize_address(address: str) -> str:
    """
    주소 정규화

    기존 pipeline.py의 norm_addr() 함수 보존
    - 괄호 내용 제거
    - 공백 제거
    - 소문자 변환
    """
    if not address:
        return ''
    s = re.sub(r'\([^)]*\)', '', str(address))
    return re.sub(r'\s+', '', s).lower()


def hash_phone(phone: str) -> str:
    """
    전화번호 해시화 (평문 저장 방지)

    보안:
    - salted SHA-256 사용
    - 원본 전화번호 복원 불가능 (단방향 해시)
    - 통계/중복 제거 용도로만 사용 가능

    Args:
        phone: 전화번호 (숫자만)

    Returns:
        16자리 해시 문자열 (빈 문자열이면 유효하지 않은 번호)
    """
    if not phone:
        return ''

    cleaned = re.sub(r'\D', '', str(phone))
    if not cleaned or len(cleaned) < 9:
        return ''

    salt = _get_salt()
    hash_input = f'{salt}:{cleaned}'
    return hashlib.sha256(hash_input.encode('utf-8')).hexdigest()[:16]


def generate_dedup_key(
    raw_name: str,
    raw_address: str,
    phone: Optional[str] = None,
    detail_address: Optional[str] = None,
) -> str:
    """
    중복 제거 키 생성

    기존 pipeline.py의 make_dedup_key() 로직 보존:
    1. 전화번호가 있으면: name + address + phone_hash
    2. 전화번호가 없으면: name + address + detail_hash

    보안 개선:
    - 기존: phone_norm을 평문으로 키에 포함
    - 개선: phone_hash(salted SHA-256)를 키에 포함

    Args:
        raw_name: 상호명
        raw_address: 주소
        phone: 전화번호 (선택)
        detail_address: 상세 주소 (선택)

    Returns:
        32자리 해시 문자열 (빈 문자열이면 생성 실패)
    """
    name = normalize_name(raw_name)
    addr = normalize_address(raw_address)

    if not name or not addr:
        return ''

    # 상세 주소 해시 (기존 로직 보존)
    detail = str(detail_address or '')
    detail_hash = hashlib.md5(detail.encode('utf-8')).hexdigest()[:8]

    # 전화번호 해시 (보안 개선)
    phone_hash = hash_phone(phone) if phone else ''

    if phone_hash:
        # 전화번호가 있으면 phone_hash 사용
        key_material = f'{name}|{addr}|{phone_hash}'
    else:
        # 전화번호가 없으면 detail_hash 사용
        key_material = f'{name}|{addr}|{detail_hash}'

    return hashlib.sha256(key_material.encode('utf-8')).hexdigest()[:32]