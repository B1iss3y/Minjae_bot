# Minjae_bot

## 운영/테스트 환경 분리

운영 봇과 테스트 봇은 반드시 서로 다른 Discord Application과 토큰을
사용해야 합니다. 같은 토큰으로 두 프로세스를 동시에 실행하지 마세요.

1. `.env.production.example`을 `.env.production`으로 복사하고 운영 봇
   토큰과 본 서버 ID를 입력합니다.
2. `.env.test.example`을 `.env.test`로 복사하고 별도로 만든 테스트 봇
   토큰과 테스트 서버 ID를 입력합니다.
3. 운영 환경은 `run_production.cmd`, 테스트 환경은 `run_test.cmd`로
   실행합니다.

각 환경은 서로 다른 DB 파일을 사용하며, `ALLOWED_GUILD_ID`에 지정한
서버에만 슬래시 명령을 동기화하고 실행합니다. Discord Developer Portal에서
두 봇 모두 필요한 Privileged Gateway Intents를 활성화해야 합니다.

새 코드를 테스트 서버에서만 검증하려면 코드 체크아웃도 분리해야 합니다.
안정 버전 체크아웃에서는 `run_production.cmd`를, 개발 버전 체크아웃에서는
`run_test.cmd`를 실행합니다. 테스트가 끝난 뒤 검증된 커밋만 운영
체크아웃에 반영합니다.
