"""도메인 데이터 모델 (관로·결함·보수구간·공법판정·수량)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# 정비공법 명칭 (보고서·도면 레이어 공통 사용)
M_UPSIZE = "굴착교체(증경)"
M_REPLACE = "굴착교체(동경)"
M_PARTIAL_DIG = "부분굴착+비굴착"
M_CIPP = "비굴착 전체보수"
M_PATCH = "비굴착 부분보수"
M_CLEAN = "준설/세정"
M_NONE = "개량제외"
METHODS = [M_UPSIZE, M_REPLACE, M_PARTIAL_DIG, M_CIPP, M_PATCH, M_CLEAN, M_NONE]


def grade_level(grade: int) -> str:
    return "대" if grade >= 5 else ("중" if grade == 4 else "소")


@dataclass
class Defect:
    pipe_id: str
    distance_raw: float                    # CCTV 조서상 거리(m)
    code: str
    name: str
    grade: int                             # 1(경미) ~ 5(심각)
    description: str = ""
    end_distance_raw: Optional[float] = None   # 연속결함 종료거리
    position_estimated: bool = False           # 위치 미상(집계형 조서 변환 등) → 근접결함 병합에서 제외
    score: int = 0                             # 적용 매뉴얼 결함점수(2025 침입수 추가배점 포함)
    bonus: int = 0                             # 침입수 추가배점(2025)
    state: str = "structural"                  # 상태등급 산정 구분(structural | operational)
    level_label: str = ""                      # 매뉴얼 등급 표기(2015: A/B/C, 2017·2025: 대/중/소)
    # ---- 판정 결과(가공 단계에서 채움) ----
    category: str = "structural"           # structural | operational
    remedy: str = "부분보수"
    is_repair_target: bool = False
    is_blocker: bool = False
    blocker_scope: str = "point"           # point | pipe
    distance_cal: float = 0.0              # 연장 보정 후 도면상 위치(m)
    end_distance_cal: Optional[float] = None

    @property
    def start_cal(self) -> float:
        if self.end_distance_cal is None:
            return self.distance_cal
        return min(self.distance_cal, self.end_distance_cal)

    @property
    def end_cal(self) -> float:
        if self.end_distance_cal is None:
            return self.distance_cal
        return max(self.distance_cal, self.end_distance_cal)

    @property
    def level(self) -> str:
        """매뉴얼 등급 표기: 5=대, 4=중, 3 이하=소"""
        return grade_level(self.grade)

    @property
    def label(self) -> str:
        return f"{self.name}({self.code}) {self.level}"


@dataclass
class Pipe:
    pipe_id: str
    from_mh: str
    to_mh: str
    material: str
    diameter_mm: int
    plan_length_m: float
    cctv_length_m: float
    avg_depth_m: float
    pavement: str = "아스콘"
    survey_direction: str = "순방향"        # 순방향(시점→종점) | 역방향
    manual: Optional[str] = None            # 적용 조사·평가 매뉴얼(2015/2017/2025). None이면 사업 기본값
    survey_length_m: Optional[float] = None # 조사길이(주행거리). 2015 평균값 분모. 없으면 촬영연장
    capacity_insufficient: bool = False
    capacity_ratio: Optional[float] = None
    required_diameter_mm: Optional[int] = None
    design_flow_lps: Optional[float] = None     # 계획(설계)유량 L/s
    roughness_n: Optional[float] = None         # Manning 조도계수(미입력 시 관종별 기본값)
    slope: Optional[float] = None               # 관저경사 (m/m, 수리검토 시 산정)
    full_capacity_lps: Optional[float] = None   # 만관 통수능 L/s
    vertices: Optional[list] = None             # GIS 선형 정점 [(x, y), ...] (작업 좌표계, 시점→종점 순)
    from_x: Optional[float] = None
    from_y: Optional[float] = None
    to_x: Optional[float] = None
    to_y: Optional[float] = None
    from_ground_el: Optional[float] = None
    from_invert_el: Optional[float] = None
    to_ground_el: Optional[float] = None
    to_invert_el: Optional[float] = None
    defects: list[Defect] = field(default_factory=list)
    # 가공 결과
    scale_factor: float = 1.0
    warnings: list[str] = field(default_factory=list)

    @property
    def has_coords(self) -> bool:
        return None not in (self.from_x, self.from_y, self.to_x, self.to_y)

    def elevations(self) -> tuple[float, float, float, float, bool]:
        """(시점지반고, 시점관저고, 종점지반고, 종점관저고, 가정값여부)"""
        assumed = False
        fg, fi, tg, ti = self.from_ground_el, self.from_invert_el, self.to_ground_el, self.to_invert_el
        if fg is None and fi is None:
            fg, assumed = 10.0, True
        if tg is None and ti is None:
            tg, assumed = 10.0, True
        if fi is None:
            fi = fg - self.avg_depth_m
        if fg is None:
            fg = fi + self.avg_depth_m
        if ti is None:
            ti = tg - self.avg_depth_m
        if tg is None:
            tg = ti + self.avg_depth_m
        return fg, fi, tg, ti, assumed


@dataclass
class RepairCluster:
    """근접결함 병합 후의 보수 구간(부분보수 또는 부분굴착)."""
    pipe_id: str
    cluster_no: int
    kind: str                       # 부분보수 | 부분굴착
    defects: list[Defect]
    span_start_m: float             # 결함 최소 위치
    span_end_m: float               # 결함 최대 위치
    repair_start_m: float = 0.0     # 시공 구간(여유 포함)
    repair_end_m: float = 0.0
    required_length_m: float = 0.0
    unit_size_m: float = 0.0        # 적용 규격(부분보수 표준규격)
    n_sites: int = 1                # 계상 개소수
    cost: float = 0.0               # 병합 적용 공사비
    separate_cost: float = 0.0      # 병합하지 않았을 경우 공사비

    @property
    def merged(self) -> bool:
        return len(self.defects) > 1

    @property
    def length_m(self) -> float:
        return self.repair_end_m - self.repair_start_m

    @property
    def max_gap_m(self) -> float:
        ds = sorted(self.defects, key=lambda d: d.start_cal)
        gaps = [b.start_cal - a.end_cal for a, b in zip(ds, ds[1:])]
        return max(gaps) if gaps else 0.0


@dataclass
class PipeDecision:
    pipe_id: str
    method: str
    detail: str
    reason: str
    defect_rate_pct: float = 0.0
    n_structural: int = 0
    n_operational: int = 0
    n_minor: int = 0
    n_lateral_cut: int = 0
    patch_clusters: list[RepairCluster] = field(default_factory=list)     # 실제 계상 부분보수
    evaluated_clusters: list[RepairCluster] = field(default_factory=list)  # 경제성 검토에 사용한 병합 구간
    dig_clusters: list[RepairCluster] = field(default_factory=list)
    patch_cost: float = 0.0
    full_cipp_cost: float = 0.0
    patch_to_full_ratio: float = 0.0
    new_diameter_mm: Optional[int] = None
    sub_method: str = ""            # 부분굴착+비굴착 시 잔여 비굴착 공법
    total_cost: float = 0.0
    manual: str = "2017"            # 판정에 적용한 매뉴얼
    condition_score: float = 1.0    # 구조적 상태점수(1.00~5.00): 2017·2025 본 등급 평균 / 2015 평균값 등급
    oper_score: float = 1.0         # 운영적 상태점수
    condition_grade: str = "1"      # 구조적 최대 등급("1"~"5"): 2017·2025 최대 본 등급 / 2015 최대값 등급
    bad_unit_ratio_pct: float = 0.0 # 2017·2025: 구조 3등급 이상 본 비율(%)
    state_text: str = ""            # 매뉴얼 표기 상태 요약(예: '평균 2.75 / 최대 4등급', '최대 40점(부실)·평균 1.16(양호)')
    bad_ratio: float = 0.0          # 2015 불량비 = 이상항목 개소수 / 연장(m)
    emergency: bool = False         # 2015 긴급보수(구조 최대값 '불량')
    priority: int = 0               # 정비 우선순위(1이 가장 시급)

    @property
    def n_patch_sites(self) -> int:
        return sum(c.n_sites for c in self.patch_clusters)


@dataclass
class QuantityItem:
    pipe_id: str
    method: str
    group: str          # 공종구분 (굴착공, 관부설공, 비굴착보수공, 부대공, 포장공, 가시설공)
    item: str
    spec: str
    unit: str
    qty: float
    unit_price: float

    @property
    def amount(self) -> float:
        return round(self.qty * self.unit_price)


@dataclass
class ProjectResult:
    pipes: list[Pipe]
    decisions: dict[str, PipeDecision]
    quantities: list[QuantityItem]
    warnings: list[str]
    config: dict
    crs: Optional[str] = None       # 관로 좌표(from_x 등)의 좌표계 (예: EPSG:5186)

    @property
    def total_cost(self) -> float:
        return sum(q.amount for q in self.quantities) * (1 + self.config["criteria"].get("overhead_ratio", 0.0))
