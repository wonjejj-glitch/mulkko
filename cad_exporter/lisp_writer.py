"""방식 B: AutoLISP(.lsp) 스크립트 생성기.

작업 중인 .dwg에서 APPLOAD로 로드 후
  GPALL  : 관로 시·종점 좌표(조서 좌표 = 도면 좌표) 기준으로 전체 결함 주석·보수구간 일괄 배치
  GPPICK : 관번 입력 → 도면의 관로(LINE/PLINE/ARC) 선택 → 선형을 따라 결함 주석 배치
           (도면 좌표가 없거나 관로가 꺾인 폴리선일 때 사용. 선형 길이에 맞춰 위치 비례 보정)
  GPLEG  : 범례 배치
"""
from __future__ import annotations

from pathlib import Path

from cad_exporter.annotate import defect_action, defect_color, defect_note, safe_layer
from common.models import M_CIPP, M_PARTIAL_DIG, M_REPLACE, M_UPSIZE, METHODS, ProjectResult


def _q(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _n(x: float) -> str:
    return f"{x:.3f}"


LISP_LIB = r'''
(vl-load-com)
(setq *GP-TH* {th} *GP-R* {r})

(defun gp:layer (nm col)
  (if (not (tblsearch "LAYER" nm))
    (entmake (list '(0 . "LAYER") '(100 . "AcDbSymbolTableRecord") '(100 . "AcDbLayerTableRecord")
                   (cons 2 nm) '(70 . 0) (cons 62 col) '(6 . "Continuous")))))

(defun gp:init ()
  (if (not (tblsearch "STYLE" "GP_KOR"))
    (entmake (list '(0 . "STYLE") '(100 . "AcDbSymbolTableRecord") '(100 . "AcDbTextStyleTableRecord")
                   '(2 . "GP_KOR") '(70 . 0) '(40 . 0.0) '(41 . 1.0) '(50 . 0.0) '(71 . 0) '(42 . 2.5)
                   (cons 3 {font}) '(4 . ""))))
  (gp:layer "GP_결함마커" 2)
  (gp:layer "GP_결함주석" 7)
  (gp:layer "GP_부분보수구간" 3)
  (gp:layer "GP_부분굴착구간" 30)
  (gp:layer "GP_관로공법" 7)
{method_layers}  (princ))

(defun gp:text (pt txt h ang lay col)
  (entmake (list '(0 . "TEXT") (cons 8 lay) (cons 62 col) (cons 10 pt) (cons 40 h) (cons 1 txt)
                 (cons 50 ang) '(7 . "GP_KOR") '(72 . 0) (cons 11 pt) '(73 . 2))))

(defun gp:line (p1 p2 lay col)
  (entmake (list '(0 . "LINE") (cons 8 lay) (cons 62 col) (cons 10 p1) (cons 11 p2))))

(defun gp:circle (pt r lay col)
  (entmake (list '(0 . "CIRCLE") (cons 8 lay) (cons 62 col) (cons 10 pt) (cons 40 r))))

(defun gp:pline (p1 p2 w lay col)
  (entmake (list '(0 . "LWPOLYLINE") '(100 . "AcDbEntity") (cons 8 lay) (cons 62 col)
                 '(100 . "AcDbPolyline") '(90 . 2) '(70 . 0) (cons 43 w)
                 (list 10 (car p1) (cadr p1)) (list 10 (car p2) (cadr p2)))))

(defun gp:arrow (tip from size lay col / a b1 b2)
  (setq a (angle tip from)
        b1 (polar (polar tip a size) (+ a (/ pi 2)) (* size 0.3))
        b2 (polar (polar tip a size) (- a (/ pi 2)) (* size 0.3)))
  (entmake (list '(0 . "SOLID") (cons 8 lay) (cons 62 col) (cons 10 tip) (cons 11 b1) (cons 12 b2) (cons 13 b2))))

;; src : ENAME(곡선) 또는 (p0 p1 len) 직선
(defun gp:clen (src)
  (if (= (type src) 'ENAME)
    (vlax-curve-getDistAtParam src (vlax-curve-getEndParam src))
    (caddr src)))

(defun gp:pos (src d / L)
  (setq L (gp:clen src) d (max 0.0 (min d L)))
  (if (= (type src) 'ENAME)
    (vlax-curve-getPointAtDist src d)
    (polar (car src) (angle (car src) (cadr src)) d)))

(defun gp:ang (src d / L prm v)
  (setq L (gp:clen src) d (max 0.0 (min d L)))
  (if (= (type src) 'ENAME)
    (progn (setq prm (vlax-curve-getParamAtDist src d) v (vlax-curve-getFirstDeriv src prm))
           (atan (cadr v) (car v)))
    (angle (car src) (cadr src))))

(defun gp:readable (a)
  (while (> a pi) (setq a (- a (* 2 pi))))
  (while (< a (- pi)) (setq a (+ a (* 2 pi))))
  (cond ((> a (/ pi 2)) (- a pi)) ((< a (- (/ pi 2))) (+ a pi)) (T a)))

;; rec = (관번 도면연장 공법 공법색 세부 좌표 결함목록 보수구간목록)
;; 결함 = (거리 종료거리|nil 색 주석)   보수구간 = (시작 끝 구분 색)
(defun gp:annotate (src rec rev / L clen sc meth mcol detail defs clus i cd pt a ra off el tl d c lay mid)
  (setq L (nth 1 rec) meth (nth 2 rec) mcol (nth 3 rec) detail (nth 4 rec)
        defs (nth 6 rec) clus (nth 7 rec) clen (gp:clen src) sc (/ clen L))
  (defun d2c (x) (if rev (- clen (* x sc)) (* x sc)))
  (setq lay (strcat "GP_공법_" (gp:safe meth)))
  ;; 공법 라벨
  (setq mid (gp:pos src (/ clen 2.0)) a (gp:ang src (/ clen 2.0)) ra (gp:readable a))
  (gp:text (polar mid (- ra (/ pi 2)) (* *GP-TH* 2.0)) (strcat (car rec) " [" meth "] " detail) *GP-TH* ra lay mcol)
  ;; 보수구간 강조
  (foreach c clus
    (gp:pline (gp:pos src (d2c (car c))) (gp:pos src (d2c (cadr c)))
              (if (= (caddr c) "부분굴착") (* *GP-R* 2.0) (* *GP-R* 1.4))
              (if (= (caddr c) "부분굴착") "GP_부분굴착구간" (if (= (caddr c) "전체") lay "GP_부분보수구간"))
              (nth 3 c)))
  ;; 결함 마커 + 지시선 + 주석
  (setq i 0)
  (foreach d defs
    (setq cd (d2c (car d)) pt (gp:pos src cd) a (gp:ang src cd) ra (gp:readable a))
    (gp:circle pt *GP-R* "GP_결함마커" (nth 2 d))
    (if (cadr d)
      (gp:pline pt (gp:pos src (d2c (cadr d))) (* *GP-R* 0.5) "GP_결함마커" (nth 2 d)))
    (setq off (* *GP-TH* (+ 3.0 (* (rem i 4) 2.2)))
          el (polar pt (+ ra (/ pi 2)) off)
          tl (polar el ra (* *GP-TH* 1.5)))
    (gp:line pt el "GP_결함주석" (nth 2 d))
    (gp:line el tl "GP_결함주석" (nth 2 d))
    (gp:arrow pt el (* *GP-TH* 0.6) "GP_결함주석" (nth 2 d))
    (gp:text (polar tl ra (* *GP-TH* 0.3)) (nth 3 d) (* *GP-TH* 0.85) ra "GP_결함주석" (nth 2 d))
    (setq i (1+ i)))
  (princ))

(defun gp:safe (s / out ch)
  (setq out "")
  (foreach ch (vl-string->list s)
    (setq out (strcat out (if (member ch '(47 92 60 62 34 58 59 63 42 124 61 96)) "_" (chr ch)))))
  out)

(defun C:GPALL (/ n skip src co)
  (gp:init)
  (setq n 0 skip 0)
  (foreach rec *GP-DATA*
    (if (setq co (nth 5 rec))
      (progn
        (setq src (list (list (nth 0 co) (nth 1 co) 0.0) (list (nth 2 co) (nth 3 co) 0.0)
                        (distance (list (nth 0 co) (nth 1 co)) (list (nth 2 co) (nth 3 co)))))
        (if (> (caddr src) 0.0) (progn (gp:annotate src rec nil) (setq n (1+ n))) (setq skip (1+ skip))))
      (setq skip (1+ skip))))
  (princ (strcat "\n[GPALL] 관로 " (itoa n) "개 주석 배치 완료, 좌표없음 " (itoa skip) "개 (GPPICK으로 배치)"))
  (princ))

(defun C:GPPICK (/ id rec ent sp rev)
  (gp:init)
  (while (/= "" (setq id (getstring T "\n관번 입력 <Enter=종료>: ")))
    (if (setq rec (assoc id *GP-DATA*))
      (if (setq ent (car (entsel (strcat "\n" id " 관로 선형(LINE/PLINE/ARC) 선택: "))))
        (progn
          (setq sp (getpoint (strcat "\n시점 맨홀(" (gp:from rec) ") 쪽 끝 클릭 <Enter=선 시작점>: ")))
          (setq rev (and sp (< (distance sp (vlax-curve-getEndPoint ent))
                               (distance sp (vlax-curve-getStartPoint ent)))))
          (gp:annotate ent rec rev)
          (princ (strcat "\n" id " 주석 배치 완료 (선형길이 " (rtos (gp:clen ent) 2 2) "m / 도면연장 "
                         (rtos (nth 1 rec) 2 2) "m 비례보정)")))
        (princ "\n선택 취소"))
      (princ (strcat "\n데이터에 없는 관번: " id))))
  (princ))

(defun gp:from (rec) (nth 8 rec))

(defun C:GPLEG (/ p i)
  (gp:init)
  (if (setq p (getpoint "\n범례 위치 지정: "))
    (progn
      (gp:text p "범례 (정비공법)" (* *GP-TH* 1.3) 0.0 "GP_관로공법" 7)
      (setq i 1)
      (foreach m *GP-METHODS*
        (gp:pline (polar p (* 1.5 pi) (* i *GP-TH* 2.2))
                  (polar (polar p (* 1.5 pi) (* i *GP-TH* 2.2)) 0.0 (* *GP-TH* 6))
                  (* *GP-TH* 0.5) (strcat "GP_공법_" (gp:safe (car m))) (cadr m))
        (gp:text (polar (polar p (* 1.5 pi) (* i *GP-TH* 2.2)) 0.0 (* *GP-TH* 7)) (car m) *GP-TH* 0.0 "GP_관로공법" 7)
        (setq i (1+ i)))))
  (princ))
'''


def build_lisp(result: ProjectResult) -> str:
    cfg = result.config
    c = cfg["cad"]
    colors = c["method_colors"]
    method_layers = "".join(
        f'  (gp:layer {_q("GP_공법_" + safe_layer(m))} {colors.get(m, 7)})\n' for m in METHODS)
    lib = (LISP_LIB.replace("{th}", _n(float(c["plan_text_height"])))
                   .replace("{r}", _n(float(c["marker_radius"])))
                   .replace("{font}", _q(c.get("font_file", "malgun.ttf")))
                   .replace("{method_layers}", method_layers))

    recs = []
    for p in result.pipes:
        dec = result.decisions[p.pipe_id]
        defs = " ".join(
            f"({_n(d.distance_cal)} {_n(d.end_distance_cal) if d.end_distance_cal is not None else 'nil'} "
            f"{defect_color(d)} {_q(defect_note(dec, d))})"
            for d in p.defects)
        clus = []
        if dec.method in (M_CIPP, M_REPLACE, M_UPSIZE):
            clus.append(f"(0.0 {_n(p.plan_length_m)} \"전체\" {colors.get(dec.method, 7)})")
        if dec.method == M_PARTIAL_DIG:
            clus += [f"({_n(cl.repair_start_m)} {_n(cl.repair_end_m)} \"부분굴착\" {colors.get(M_PARTIAL_DIG, 30)})"
                     for cl in dec.dig_clusters]
        clus += [f"({_n(cl.repair_start_m)} {_n(cl.repair_end_m)} \"부분보수\" 3)" for cl in dec.patch_clusters]
        coords = (f"({_n(p.from_x)} {_n(p.from_y)} {_n(p.to_x)} {_n(p.to_y)})" if p.has_coords else "nil")
        recs.append(
            f"  ({_q(p.pipe_id)} {_n(p.plan_length_m)} {_q(dec.method)} {colors.get(dec.method, 7)} {_q(dec.detail)}\n"
            f"   {coords}\n   ({defs})\n   ({' '.join(clus)})\n   {_q(p.from_mh)})")
    methods = " ".join(f"({_q(m)} {colors.get(m, 7)})" for m in METHODS)

    header = (
        ";;; ==========================================================================\n"
        ";;;  노후하수관로 정비 - 결함 주석 / 정비공법 자동 배치 스크립트 (자동 생성)\n"
        ";;;  사용법 : APPLOAD 로 본 파일 로드 후 명령 실행\n"
        ";;;     GPALL  - 조서 좌표 기준 전체 관로 결함 주석·보수구간 일괄 배치\n"
        ";;;     GPPICK - 관번 입력 후 도면의 관로 선형을 선택하여 배치(좌표 불일치·꺾인 관로)\n"
        ";;;     GPLEG  - 정비공법 범례 배치\n"
        f";;;  관로 {len(result.pipes)}개 / 결함 {sum(len(p.defects) for p in result.pipes)}개소\n"
        ";;;  ※ 한글 깨짐 시: 파일 인코딩(cp949 ↔ UTF-8)과 LISPSYS 설정을 확인하십시오.\n"
        ";;; ==========================================================================\n"
    )
    data = "(setq *GP-DATA* '(\n" + "\n".join(recs) + "\n))\n"
    data += f"(setq *GP-METHODS* '({methods}))\n"
    footer = '\n(princ "\\n[노후관로 결함주석] 로드 완료 - 명령: GPALL / GPPICK / GPLEG")\n(princ)\n'
    return header + lib + "\n" + data + footer


def export_lisp(result: ProjectResult, path: str | Path, encoding: str | None = None) -> Path:
    enc = encoding or result.config["cad"].get("lsp_encoding", "cp949")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = build_lisp(result).replace("\n", "\r\n")
    with open(path, "w", encoding=enc, errors="replace", newline="") as f:
        f.write(text)
    return path


__all__ = ["build_lisp", "export_lisp", "defect_action"]
