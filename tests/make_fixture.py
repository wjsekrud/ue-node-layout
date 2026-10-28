"""Builds an 'everything in one line' Blueprint T3D fixture, like MCP-created graphs."""
import random
import sys

random.seed(1)
G = lambda: "%032X" % random.getrandbits(128)
nodes = []
ids = {}


def pid(k):
    ids.setdefault(k, G())
    return ids[k]


def P(owner, name, cat, out=False, links=(), hidden=False):
    return dict(id=pid(owner + "." + name), name=name, cat=cat, out=out,
                links=[(n, pid(n + "." + p)) for n, p in links], hidden=hidden)


def node(name, cls, extra, pins):
    nodes.append((name, "/Script/BlueprintGraph." + cls, extra, pins))


FN = 'FunctionReference=(MemberName="%s")'
E0, IF, VT, ISV, GN, AP, P1, P2, DL, GL, MV, AD, SL = (
    "K2Node_Event_0", "K2Node_IfThenElse_0", "K2Node_VariableGet_0", "K2Node_CallFunction_0",
    "K2Node_CallFunction_1", "K2Node_CallFunction_2", "K2Node_CallFunction_3", "K2Node_CallFunction_4",
    "K2Node_CallFunction_5", "K2Node_CallFunction_6", "K2Node_CallFunction_7", "K2Node_CallFunction_8",
    "K2Node_CallFunction_9")
E1, SQ, P3, P4 = "K2Node_Event_1", "K2Node_ExecutionSequence_0", "K2Node_CallFunction_10", "K2Node_CallFunction_11"

node(E0, "K2Node_Event", 'EventReference=(MemberName="ReceiveBeginPlay")',
     [P(E0, "then", "exec", True, [(IF, "execute")])])
node(IF, "K2Node_IfThenElse", "",
     [P(IF, "execute", "exec", False, [(E0, "then")]),
      P(IF, "Condition", "bool", False, [(ISV, "ReturnValue")]),
      P(IF, "then", "exec", True, [(P1, "execute")]),
      P(IF, "else", "exec", True, [(P2, "execute")])])
node(VT, "K2Node_VariableGet", 'VariableReference=(MemberName="TargetActor",bSelfContext=True)',
     [P(VT, "TargetActor", "object", True, [(ISV, "Object"), (GN, "Object")])])
node(ISV, "K2Node_CallFunction", FN % "IsValid",
     [P(ISV, "Object", "object", False, [(VT, "TargetActor")]),
      P(ISV, "ReturnValue", "bool", True, [(IF, "Condition")])])
node(GN, "K2Node_CallFunction", FN % "GetObjectName",
     [P(GN, "Object", "object", False, [(VT, "TargetActor")]),
      P(GN, "ReturnValue", "string", True, [(AP, "A")])])
node(AP, "K2Node_CallFunction", FN % "Concat_StrStr",
     [P(AP, "A", "string", False, [(GN, "ReturnValue")]),
      P(AP, "B", "string"),
      P(AP, "ReturnValue", "string", True, [(P1, "InString")])])
node(P1, "K2Node_CallFunction", FN % "PrintString",
     [P(P1, "execute", "exec", False, [(IF, "then")]),
      P(P1, "then", "exec", True, [(DL, "execute")]),
      P(P1, "InString", "string", False, [(AP, "ReturnValue")]),
      P(P1, "Duration", "real", False, [], True)])
node(P2, "K2Node_CallFunction", FN % "PrintString",
     [P(P2, "execute", "exec", False, [(IF, "else")]),
      P(P2, "then", "exec", True),
      P(P2, "InString", "string")])
node(DL, "K2Node_CallFunction", FN % "Delay",
     [P(DL, "execute", "exec", False, [(P1, "then")]),
      P(DL, "then", "exec", True, [(SL, "execute")]),
      P(DL, "Duration", "real")])
node(GL, "K2Node_CallFunction", FN % "K2_GetActorLocation",
     [P(GL, "ReturnValue", "struct", True, [(AD, "A")])])
node(MV, "K2Node_CallFunction", FN % "MakeVector",
     [P(MV, "X", "real"), P(MV, "Y", "real"), P(MV, "Z", "real"),
      P(MV, "ReturnValue", "struct", True, [(AD, "B")])])
node(AD, "K2Node_CallFunction", FN % "Add_VectorVector",
     [P(AD, "A", "struct", False, [(GL, "ReturnValue")]),
      P(AD, "B", "struct", False, [(MV, "ReturnValue")]),
      P(AD, "ReturnValue", "struct", True, [(SL, "NewLocation")])])
node(SL, "K2Node_CallFunction", FN % "K2_SetActorLocation",
     [P(SL, "execute", "exec", False, [(DL, "then")]),
      P(SL, "then", "exec", True),
      P(SL, "NewLocation", "struct", False, [(AD, "ReturnValue")]),
      P(SL, "bSweep", "bool"),
      P(SL, "ReturnValue", "bool", True)])
node(E1, "K2Node_Event", 'EventReference=(MemberName="ReceiveTick")',
     [P(E1, "then", "exec", True, [(SQ, "execute")]),
      P(E1, "DeltaSeconds", "real", True, [(P3, "Duration")])])
node(SQ, "K2Node_ExecutionSequence", "",
     [P(SQ, "execute", "exec", False, [(E1, "then")]),
      P(SQ, "then_0", "exec", True, [(P3, "execute")]),
      P(SQ, "then_1", "exec", True, [(P4, "execute")])])
node(P3, "K2Node_CallFunction", FN % "PrintString",
     [P(P3, "execute", "exec", False, [(SQ, "then_0")]),
      P(P3, "then", "exec", True),
      P(P3, "Duration", "real", False, [(E1, "DeltaSeconds")])])
node(P4, "K2Node_CallFunction", FN % "PrintString",
     [P(P4, "execute", "exec", False, [(SQ, "then_1")]),
      P(P4, "then", "exec", True)])

out = []
for i, (name, cls, extra, pins) in enumerate(nodes):
    out.append(f'Begin Object Class={cls} Name="{name}" ExportPath="{cls}\'/Game/BP_Test.BP_Test:EventGraph.{name}\'"')
    if extra:
        out.append("   " + extra)
    if i:  # node 0 sits at x=0, so UE omits NodePosX for it
        out.append(f"   NodePosX={i * 300}")
    out.append(f"   NodeGuid={G()}")
    for p in pins:
        s = (f'   CustomProperties Pin (PinId={p["id"]},PinName="{p["name"]}",PinType.PinCategory="{p["cat"]}",'
             'PinType.PinSubCategory="",PinType.PinSubCategoryObject=None,')
        if p["out"]:
            s += 'Direction="EGPD_Output",'
        if p["links"]:
            s += "LinkedTo=(" + "".join(f"{n} {q}," for n, q in p["links"]) + "),"
        s += "PersistentGuid=00000000000000000000000000000000,bHidden=%s,bNotConnectable=False,)" % (
            "True" if p["hidden"] else "False")
        out.append(s)
    out.append("End Object")
sys.stdout.buffer.write(("\r\n".join(out) + "\r\n").encode("utf-8"))
