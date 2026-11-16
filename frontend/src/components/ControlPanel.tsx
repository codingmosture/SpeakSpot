import { useRef, useState } from 'react'

interface Props {
  canSearch: boolean
  onSubmitText: (text: string) => void
  autoFollow: boolean
  onAutoFollowChange: (value: boolean) => void
}

export function ControlPanel({ canSearch, onSubmitText, autoFollow, onAutoFollowChange }: Props) {
  const [text, setText] = useState('')
  return (
    <section className="search-panel" aria-labelledby="search-title">
      <h2 id="search-title">문서에서 찾기</h2>
      <form
        className="text-form"
        onSubmit={(e) => {
          e.preventDefault()
          const value = text.trim()
          if (!value || !canSearch) return
          onSubmitText(value)
          setText('')
        }}
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="찾을 내용을 입력하세요"
          aria-label="찾을 내용"
        />
        <button type="submit" className="btn primary" disabled={!text.trim() || !canSearch}>
          찾기
        </button>
      </form>
      <label className="toggle follow">
        <input
          type="checkbox"
          checked={autoFollow}
          onChange={(e) => onAutoFollowChange(e.target.checked)}
        />
        매칭 위치로 자동 페이지 이동
      </label>
    </section>
  )
}


interface DocumentProps {
  fileName: string | null
  pageCount: number
  documentBusy: boolean
  sttActive: boolean
  onFileSelect: (file: File) => void
}

export function DocumentPanel({ fileName, pageCount, documentBusy, sttActive, onFileSelect }: DocumentProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  return (
    <section className="document-panel" aria-labelledby="document-title" aria-busy={documentBusy}>
      <div className="document-heading"><h2 id="document-title">문서</h2><span>{pageCount > 0 ? `${pageCount}페이지` : 'PDF'}</span></div>
      <div className="file-row">
        <span className="file-name" title={fileName ?? undefined}>
          {documentBusy ? '업로드·분석 중…' : (fileName ?? 'PDF 문서를 선택하세요')}
        </span>
        <button
          type="button"
          className="btn ghost"
          disabled={documentBusy || sttActive}
          onClick={() => fileInputRef.current?.click()}
        >
          PDF 업로드
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="application/pdf"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) onFileSelect(file)
            e.target.value = ''
          }}
        />
      </div>
    </section>
  )
}
