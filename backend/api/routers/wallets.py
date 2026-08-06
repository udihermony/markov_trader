from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.db.models import Strategy, User, Wallet

router = APIRouter(prefix="/wallets", tags=["wallets"])

# DESIGN.md §3: "A SPY buy-and-hold wallet is created by default and cannot
# be deleted." `always`/`never` are the two buy-and-hold primitive node
# types added in this milestone specifically for this spec.
DEFAULT_BENCHMARK_SPEC = {
    "spec_version": 2,
    "name": "SPY Buy & Hold",
    "sources": [{"id": "px", "type": "price_bars"}],
    "nodes": [
        {"id": "u1", "kind": "universe", "type": "manual_list", "params": {"tickers": ["SPY"]}},
        {"id": "t1", "kind": "trigger", "type": "always", "params": {}},
        {"id": "x1", "kind": "exit", "type": "never", "params": {}},
        {"id": "s1", "kind": "size", "type": "fixed_fraction", "params": {"fraction": 1.0}},
    ],
    "edges": [["u1", "t1"]],
}
DEFAULT_BENCHMARK_INITIAL_CASH = 100_000.0


class CreateWalletRequest(BaseModel):
    name: str
    strategy_id: int
    initial_cash: float = 100_000.0
    start_date: date | None = None


class WalletResponse(BaseModel):
    id: int
    name: str
    strategy_id: int | None
    initial_cash: float
    cash: float
    start_date: date
    status: str
    is_benchmark: bool
    created_at: datetime
    retired_at: datetime | None


def _to_response(wallet: Wallet) -> WalletResponse:
    return WalletResponse(
        id=wallet.id, name=wallet.name, strategy_id=wallet.strategy_id,
        initial_cash=float(wallet.initial_cash), cash=float(wallet.cash),
        start_date=wallet.start_date, status=wallet.status, is_benchmark=wallet.is_benchmark,
        created_at=wallet.created_at, retired_at=wallet.retired_at,
    )


def create_default_benchmark_wallet(db: Session, user: User) -> Wallet:
    strategy = Strategy(
        user_id=user.id, name=DEFAULT_BENCHMARK_SPEC["name"],
        spec_json=DEFAULT_BENCHMARK_SPEC, spec_version=DEFAULT_BENCHMARK_SPEC["spec_version"],
    )
    db.add(strategy)
    db.flush()
    wallet = Wallet(
        user_id=user.id, name="SPY Benchmark", strategy_id=strategy.id,
        initial_cash=DEFAULT_BENCHMARK_INITIAL_CASH, cash=DEFAULT_BENCHMARK_INITIAL_CASH,
        start_date=date.today(), status="active", is_benchmark=True,
    )
    db.add(wallet)
    db.flush()
    return wallet


@router.post("", response_model=WalletResponse, status_code=status.HTTP_201_CREATED)
def create_wallet(
    payload: CreateWalletRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WalletResponse:
    strategy = db.execute(
        select(Strategy).where(Strategy.id == payload.strategy_id, Strategy.user_id == user.id)
    ).scalar_one_or_none()
    if strategy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="strategy not found")

    start_date = payload.start_date or date.today()
    if start_date < date.today():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="wallets cannot be backdated"
        )

    wallet = Wallet(
        user_id=user.id, name=payload.name, strategy_id=strategy.id,
        initial_cash=payload.initial_cash, cash=payload.initial_cash,
        start_date=start_date, status="active", is_benchmark=False,
    )
    db.add(wallet)
    db.commit()
    db.refresh(wallet)
    return _to_response(wallet)


@router.get("", response_model=list[WalletResponse])
def list_wallets(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[WalletResponse]:
    wallets = db.execute(
        select(Wallet).where(Wallet.user_id == user.id).order_by(Wallet.id)
    ).scalars().all()
    return [_to_response(w) for w in wallets]


@router.get("/{wallet_id}", response_model=WalletResponse)
def get_wallet(
    wallet_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> WalletResponse:
    wallet = db.execute(
        select(Wallet).where(Wallet.id == wallet_id, Wallet.user_id == user.id)
    ).scalar_one_or_none()
    if wallet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="wallet not found")
    return _to_response(wallet)


@router.post("/{wallet_id}/retire", response_model=WalletResponse)
def retire_wallet(
    wallet_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> WalletResponse:
    wallet = db.execute(
        select(Wallet).where(Wallet.id == wallet_id, Wallet.user_id == user.id)
    ).scalar_one_or_none()
    if wallet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="wallet not found")
    if wallet.is_benchmark:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="the benchmark wallet cannot be retired"
        )
    if wallet.status != "retired":
        wallet.status = "retired"
        wallet.retired_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(wallet)
    return _to_response(wallet)
