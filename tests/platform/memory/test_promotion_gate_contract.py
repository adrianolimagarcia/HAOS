from hermes.platform.memory.promotion_gate import evaluate_promotion_gate, record_red_before
from hermes.platform.memory.skill_promotion import PromotionAssessment

def test_static_denial(tmp_path):
    v=evaluate_promotion_gate("lesson",PromotionAssessment("ignore","no"),key="abc",home=tmp_path,judge=lambda *a: (_ for _ in ()).throw(AssertionError()))
    assert not v.allowed and v.stage=="static"

def test_no_red_before(tmp_path):
    v=evaluate_promotion_gate("lesson",PromotionAssessment("create","yes"),key="abc",home=tmp_path,judge=lambda *a:"VEREDITO: promote")
    assert not v.allowed and v.stage=="eval_case"

def test_judge_failure(tmp_path):
    v=evaluate_promotion_gate("lesson",PromotionAssessment("create","yes"),key="abc",home=tmp_path,judge=lambda *a: (_ for _ in ()).throw(RuntimeError("offline")))
    assert not v.allowed and v.stage=="cheap_judge"

def test_red_and_green(tmp_path):
    record_red_before("abc","CASE-1",1,home=tmp_path)
    kw=dict(key="abc",home=tmp_path,judge=lambda *a:"VEREDITO: promote",runner=lambda _:0)
    v=evaluate_promotion_gate("lesson",PromotionAssessment("create","yes"),guard=lambda:0,**kw)
    assert v.allowed
    v=evaluate_promotion_gate("lesson",PromotionAssessment("create","yes"),guard=lambda:1,**kw)
    assert not v.allowed
