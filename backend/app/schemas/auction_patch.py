from typing import Optional
from pydantic import BaseModel

class BidCreate(BaseModel):
    amount: int
    captain_id: Optional[int] = None  # Auctioneer places bid on behalf of captain

class BidUpdateAmount(BaseModel):
    amount: int
