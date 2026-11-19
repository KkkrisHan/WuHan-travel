import re
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .assistant import chat, simple_patch
from .models import Snapshot


class ChatState(TypedDict, total=False):
    snapshot: Snapshot
    message: str
    locked: bool
    answer: str
    patch: dict | None
    action: str | None


async def respond(state: ChatState) -> dict:
    if not state["locked"] and state["snapshot"].itinerary and re.search(r"优化当前行程|重排行程|重新规划当前行程", state["message"]):
        return {"answer": "正在生成当前行程的优化预览。原行程会保持不变，确认后才会应用。", "patch": None, "action": "optimization"}
    patch = None if state["locked"] else simple_patch(state["snapshot"], state["message"])
    if patch:
        return {"answer": f"正在应用：从行程中移除{patch['placeName']}。", "patch": patch}
    return {"answer": await chat(state["snapshot"], state["message"]), "patch": None}


graph = (StateGraph(ChatState)
         .add_node("respond", respond)
         .add_edge(START, "respond")
         .add_edge("respond", END)
         .compile())
