/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 'mock' | 'legacy' | 'server' (기본: pnpm dev 는 mock, 배포용 빌드는 server) */
  readonly VITE_API_MODE?: string
  /** 백엔드 주소 (기본: 같은 origin + Vite 프록시) */
  readonly VITE_API_ORIGIN?: string
  /** 'true' | 'false' — 개발 도구 표시 (기본: pnpm dev 는 true, 배포용 빌드는 false) */
  readonly VITE_DEV_TOOLS?: string
}
