"""
Supabase 적재 통합 테스트
==========================
Supabase RPC 호출을 Mock하여 적재 로직을 검증합니다.
"""
from __future__ import annotations

from unittest.mock import Mock, patch, MagicMock
import tempfile

import pytest

from bareun_pipeline.gold.transform import GoldTransformer, GoldRestaurant
from bareun_pipeline.gold.supabase_loader import SupabaseLoader, load_records


class TestSupabaseLoader:
    """Supabase 적재 로더 테스트"""

    @pytest.fixture
    def sample_gold_records(self, sample_cert_records):
        """Gold 레코드 샘플 생성"""
        transformer = GoldTransformer()
        return transformer.transform(sample_cert_records)

    @patch('bareun_pipeline.gold.supabase_loader.create_client')
    def test_load_records_success(self, mock_create_client, sample_gold_records):
        """성공적인 적재 테스트"""
        # Mock Supabase 클라이언트 설정
        mock_client = MagicMock()
        mock_rpc_response = Mock()
        mock_rpc_response.data = 2  # 2개 레코드 적재
        mock_client.rpc.return_value.execute.return_value = mock_rpc_response
        mock_create_client.return_value = mock_client

        # 적재 실행
        loader = SupabaseLoader(supabase_client=mock_client)
        result = loader.load(sample_gold_records)

        # 검증
        assert result['loaded'] == 2
        assert result['chunks'] == 1  # 2개 레코드는 1개 청크
        assert result['errors'] == []

        @patch('bareun_pipeline.gold.supabase_loader.create_client')
        def test_load_records_with_retry(self, mock_create_client, sample_gold_records):
            """재시도 로직 테스트"""
            # Mock Supabase 클라이언트 설정 (첫 2번 실패, 3번째 성공)
            mock_client = MagicMock()
            mock_rpc = MagicMock()

            # bulk_upsert: 첫 2번은 예외 발생, 3번째 성공
            # analyze: 별도 호출
            def side_effect(*args, **kwargs):
                call_count = mock_rpc.execute.call_count
                if call_count <= 2:
                    raise Exception("Network error")
                return Mock(data=2)

            mock_rpc.execute.side_effect = side_effect
            mock_client.rpc.return_value = mock_rpc
            mock_create_client.return_value = mock_client

            # 적재 실행
            loader = SupabaseLoader(supabase_client=mock_client)
            result = loader.load(sample_gold_records)

            # 검증: loaded는 2개
            assert result['loaded'] == 2

            # rpc 호출 횟수 검증:
            # bulk_upsert 3번 (실패 2 + 성공 1) + analyze 1번 = 총 4번
            # 하지만 mock_rpc는 동일한 객체이므로 execute 호출 횟수로 검증
            assert mock_rpc.execute.call_count >= 3  # 최소 3번 (재시도 포함)

    @patch('bareun_pipeline.gold.supabase_loader.create_client')
    def test_load_records_empty(self, mock_create_client):
        """빈 레코드 적재 테스트"""
        mock_client = MagicMock()
        mock_create_client.return_value = mock_client

        loader = SupabaseLoader(supabase_client=mock_client)
        result = loader.load([])

        assert result['loaded'] == 0
        assert result['chunks'] == 0
        assert result['errors'] == []
        mock_client.rpc.assert_not_called()

    @patch('bareun_pipeline.gold.supabase_loader.create_client')
    def test_load_records_chunking(self, mock_create_client):
        """청크 분할 적재 테스트"""
        # 100개 레코드 생성 (모든 필수/선택 필드 명시)
        records = []
        for i in range(100):
            records.append(
                GoldRestaurant(
                    dedup_key=f'test_key_{i}',
                    data_source='test',
                    store_name=f'테스트식당{i}',
                    province='제주특별자치도',
                    municipality='제주시',
                    emd='일도일동',
                    address_road=f'테스트로 {i}',
                    address_lotnum=None,
                    lat=33.4996,
                    lng=126.5312,
                    main_menu=f'테스트메뉴{i}',
                    main_menu_source='public_api',
                    menus=[f'서브메뉴{i}'],
                    menus_source='public_api',
                    phone='0641234567',
                    biz_type='한식',
                    quality_score=80,
                )
            )

        # Mock 설정
        mock_client = MagicMock()
        mock_rpc_response = Mock()
        mock_rpc_response.data = 50  # 각 청크 50개
        mock_client.rpc.return_value.execute.return_value = mock_rpc_response
        mock_create_client.return_value = mock_client

        # 적재 실행
        loader = SupabaseLoader(supabase_client=mock_client)
        result = loader.load(records)

        # 검증 (청크 크기 500이므로 100개는 1개 청크)
        assert result['chunks'] == 1
        assert result['loaded'] == 50

    @patch('bareun_pipeline.gold.supabase_loader.create_client')
    def test_analyze_called_after_load(self, mock_create_client, sample_gold_records):
        """적재 후 ANALYZE 호출 테스트"""
        mock_client = MagicMock()
        mock_rpc_response = Mock()
        mock_rpc_response.data = 2
        mock_client.rpc.return_value.execute.return_value = mock_rpc_response
        mock_create_client.return_value = mock_client

        loader = SupabaseLoader(supabase_client=mock_client)
        loader.load(sample_gold_records)

        # ANALYZE 호출 확인 (bulk_upsert + analyze = 2번 호출)
        assert mock_client.rpc.call_count == 2