from decimal import Decimal
from typing import Literal
from pydantic import BaseModel,Field,ConfigDict
class Clean(BaseModel):model_config=ConfigDict(str_strip_whitespace=True)
class GradeSuggestion(Clean):
    sample_id:int=Field(gt=0)
    to_grade:Literal['VLSFO','MGO']
