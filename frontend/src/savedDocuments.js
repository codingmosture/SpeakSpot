const DOCUMENT_STORES = ['pdfs', 'sessions', 'blocks']

// IndexedDB 요청의 성공과 실패를 Promise로 전달한다.
function requestResult(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

// 저장소를 열고 트랜잭션이 끝날 때까지 기다린 뒤 연결을 닫는다.
async function withLibrary(stores, mode, useStores) {
  const opening = indexedDB.open('speakspot', 3)
  // 첫 실행에는 PDF와 세션 저장소를 만들고 이전 버전에는 블럭 저장소를 추가한다.
  opening.onupgradeneeded = event => {
    if (event.oldVersion === 0) {
      opening.result.createObjectStore('pdfs')
      opening.result.createObjectStore('sessions', { keyPath: 'key' })
    }
    if (event.oldVersion < 2) opening.result.createObjectStore('blocks')
    opening.result.createObjectStore('analyses')
  }
  const db = await requestResult(opening)
  try {
    const transaction = db.transaction(stores, mode)
    const committed = new Promise((resolve, reject) => {
      transaction.oncomplete = resolve
      transaction.onabort = () => reject(transaction.error)
    })
    try {
      const operation = (async () => useStores(transaction))()
      const [result] = await Promise.all([operation, committed])
      return result
    } catch (error) {
      try { transaction.abort() } catch { /* 브라우저가 이미 트랜잭션을 중단했을 수 있다. */ }
      throw error
    }
  } finally {
    db.close()
  }
}

// 파일 내용의 SHA-256 해시로 같은 PDF의 저장본을 찾는다.
export async function pdfKey(data) {
  const hash = await crypto.subtle.digest('SHA-256', data)
  return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('')
}

// PDF를 다시 업로드했을 때 이전 페이지와 필기를 읽는다.
export function readSession(key) {
  return withLibrary('sessions', 'readonly', tx => requestResult(tx.objectStore('sessions').get(key)))
}

// 최근 목록에서 선택한 PDF 원본, 세션, 블럭 좌표를 함께 읽는다.
export function readSavedDocument(key) {
  return withLibrary(DOCUMENT_STORES, 'readonly', async tx => {
    const [file, session, blocks] = await Promise.all([
      requestResult(tx.objectStore('pdfs').get(key)),
      requestResult(tx.objectStore('sessions').get(key)),
      requestResult(tx.objectStore('blocks').get(key)),
    ])
    if (!file || !session) throw new Error('저장한 PDF를 찾을 수 없습니다.')
    return { file, session, blocks }
  })
}

// 기존 저장본을 다시 분석한 결과에서 블럭 좌표만 별도로 저장한다.
export function cacheSavedBlocks(key, blocks) {
  return withLibrary(['sessions', 'blocks'], 'readwrite', async tx => {
    const savedKey = await requestResult(tx.objectStore('sessions').getKey(key))
    if (savedKey !== undefined) await requestResult(tx.objectStore('blocks').put(blocks, key))
  })
}

// PDF를 다시 분석하지 않을 수 있도록 브라우저에 저장한 탐색 색인을 읽는다.
export function readSavedAnalysis(key) {
  return withLibrary('analyses', 'readonly', tx => requestResult(tx.objectStore('analyses').get(key)))
}

// PDF가 최근 목록에 남아 있을 때만 해당 문서의 탐색 색인을 저장한다.
export function cacheSavedAnalysis(key, snapshot) {
  return withLibrary(['sessions', 'analyses'], 'readwrite', async tx => {
    const savedKey = await requestResult(tx.objectStore('sessions').getKey(key))
    if (savedKey !== undefined) await requestResult(tx.objectStore('analyses').put(snapshot, key))
  })
}

// 최근 저장 순서로 PDF 이름과 마지막 페이지를 반환한다.
export function listSavedDocuments() {
  return withLibrary('sessions', 'readonly', async tx => {
    const sessions = await requestResult(tx.objectStore('sessions').getAll())
    return sessions.sort((a, b) => b.updatedAt - a.updatedAt).map(({ key, name, page }) => ({ key, name, page }))
  })
}

// 같은 PDF에 속한 원본, 세션, 블럭 좌표와 탐색 색인을 한 번에 삭제한다.
export function deleteSavedDocument(key) {
  const stores = [...DOCUMENT_STORES, 'analyses']
  return withLibrary(stores, 'readwrite', tx => {
    const deletes = stores.map(name => tx.objectStore(name).delete(key))
    return Promise.all(deletes.map(requestResult))
  })
}

// PDF 세션에서 선택한 필기만 제거하고 나머지 저장 데이터를 돌려준다.
export function deleteSavedNote(key, noteId) {
  return withLibrary('sessions', 'readwrite', async tx => {
    const sessions = tx.objectStore('sessions')
    const session = await requestResult(sessions.get(key))
    if (!session) return []
    const notes = session.notes.filter(note => note.id !== noteId)
    if (notes.length !== session.notes.length) await requestResult(sessions.put({ ...session, notes }))
    return notes
  })
}

// 필기 변경분을 병합하고 현재 페이지와 PDF 데이터를 함께 저장한다.
export function saveSession({ key, file, blocks }, page, note) {
  return withLibrary(DOCUMENT_STORES, 'readwrite', async tx => {
    const pdfs = tx.objectStore('pdfs'), sessions = tx.objectStore('sessions')
    const blockCache = tx.objectStore('blocks')
    const [existing, fileKey, blockKey] = await Promise.all([
      requestResult(sessions.get(key)),
      requestResult(pdfs.getKey(key)),
      blocks && requestResult(blockCache.getKey(key)),
    ])
    const notes = existing?.notes ?? []
    if (note) {
      const index = notes.findIndex(item => item.id === note.id)
      if (index === -1) notes.push({ text: '', ...note })
      else notes[index] = { ...notes[index], ...note }
    }
    const session = { key, name: file.name, page, notes, updatedAt: Date.now() }
    const writes = [sessions.put(session)]
    if (fileKey === undefined || existing?.name !== file.name) writes.push(pdfs.put(file, key))
    if (blocks && blockKey === undefined) writes.push(blockCache.put(blocks, key))
    await Promise.all(writes.map(requestResult))
    return session
  })
}
