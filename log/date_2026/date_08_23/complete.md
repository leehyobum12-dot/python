# 완료
## 리팩토링(DIP, SRP)
모듈 경로                         |       책임            
domain/address.py                 주소 파싱/정규화 (2026.08 기준)
adapters/base.py                  Circuit Breaker, HTTP 세션
adapters/geocode_client.py        네이버 지오코딩 어댑터
adapters/local_search_client.py   네이버/카카오 검색 어댑터
silver/enrich/geocode.py          지오코딩 오케스트레이션
silver/enrich/store_verify.py     가게명 검증 오케스트레이션
silver/enrich/menu.py             메뉴 보강 오케스트레이션
silver/enrich/store_info.py       가게 정보 보강 오케스트레이션
silver/enrich/__init__.py         Silver Enrichment 통합 진입점
adapters/tourist_info/client.py   관광공사 HTTP 클라이언트
adapters/tourist_info/mapper.py   관광공사 도메인 매핑
adapters/tourist_info/__init__.py 관광공사 오케스트레이터