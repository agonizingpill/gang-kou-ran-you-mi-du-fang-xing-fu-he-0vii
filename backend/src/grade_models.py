"""Whole-tank grade changes keep historical sample grades immutable.

SampleGrade snapshots the grade a sample was taken/judged under.  Existing
reference samples therefore keep their original grade even after the tank is
re-graded, and historical lab results keep showing the grade of their time.
TankGradeChange stores the target-grade re-judgment evidence and the atomic
confirmation that switches the tank grade and voids unsigned old-grade
transfers together.
"""
from decimal import Decimal
from sqlalchemy import String,Numeric,ForeignKey,JSON,CheckConstraint
from sqlalchemy.orm import Mapped,mapped_column
from .database import Base
class SampleGrade(Base):
    __tablename__='sample_grades'
    id:Mapped[int]=mapped_column(primary_key=True)
    sample_id:Mapped[int]=mapped_column(ForeignKey('fuel_samples.id'),unique=True)
    intake_grade:Mapped[str]=mapped_column(String(20))
    accepted_grade:Mapped[str|None]=mapped_column(String(20),nullable=True)
class TankGradeChange(Base):
    __tablename__='tank_grade_changes'
    __table_args__=(CheckConstraint('tank_liters_snapshot >= 0'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    tank_id:Mapped[int]=mapped_column(ForeignKey('tanks.id'))
    sample_id:Mapped[int]=mapped_column(ForeignKey('fuel_samples.id'))
    from_grade:Mapped[str]=mapped_column(String(20))
    to_grade:Mapped[str]=mapped_column(String(20))
    state:Mapped[str]=mapped_column(String(20),default='suggested')
    evidence:Mapped[dict]=mapped_column(JSON)
    tank_liters_snapshot:Mapped[Decimal]=mapped_column(Numeric(14,3))
    requested_by:Mapped[str]=mapped_column(String(120))
    confirmed_by:Mapped[str|None]=mapped_column(String(120),nullable=True)
    void_reason:Mapped[str]=mapped_column(String(600),default='')
    confirmation_snapshot:Mapped[dict|None]=mapped_column(JSON,nullable=True)
