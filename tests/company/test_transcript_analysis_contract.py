from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
import unittest
from quant_data.company.transcript_analysis_contract import validate_analysis, validate_review, question_inventory
from quant_data.errors import ValidationError

ROOT=Path(__file__).resolve().parents[2]
EXAMPLE=json.loads((ROOT/"docs/rebuild/TRANSCRIPT_ANALYSIS_V1.example.json").read_text())

def model():
    return deepcopy(EXAMPLE["model_output"])
def turns():
    return deepcopy(EXAMPLE["input"]["turns"])
def insufficient():
    return {"sentiment":"insufficient_evidence","expressed_confidence":"insufficient_evidence","hedging":"insufficient_evidence","assessment_support":"insufficient","summary":None,"supporting_evidence":[],"counterevidence":[],"ambiguities":[]}

class TranscriptAnalysisContractTests(unittest.TestCase):
    def test_example_derives_mixed_tone_and_focus(self):
        result=validate_analysis(model(),turns())
        self.assertEqual(result["derived"]["total_question_block_count"],2)
        self.assertEqual(result["derived"]["ranked_topics"][0]["question_block_share"],"1")
        self.assertEqual(result["model_output"]["management_tone"]["overall"]["sentiment"],"mixed")
        self.assertEqual(result["evidence"][0]["char_start"],0)
        self.assertIsNot(result["model_output"],EXAMPLE["model_output"])

    def test_rejects_ambiguous_and_wrong_scope_citations(self):
        output=model()
        output["guidance_claims"][0]["evidence"][0]["quote"]="gross margin"
        source=turns()
        source[0]["text"]+=" gross margin"
        with self.assertRaises(ValidationError):
            validate_analysis(output,source)
        output=model()
        output["guidance_claims"][0]["evidence"][0]["turn_id"]="t2"
        with self.assertRaises(ValidationError):
            validate_analysis(output,turns())

    def test_rejects_omitted_and_split_question_blocks(self):
        output=model()
        output["analyst_focus"]["question_blocks"]=output["analyst_focus"]["question_blocks"][:1]
        output["analyst_focus"]["topics"][0]["question_local_ids"]=["q1"]
        with self.assertRaises(ValidationError):
            validate_analysis(output,turns())
        output=model()
        output["analyst_focus"]["question_blocks"][0]["analyst_turn_ids"]=["t2","t4"]
        with self.assertRaises(ValidationError):
            validate_analysis(output,turns())

    def test_no_qa_requires_absent_and_missing_qa_tone_insufficient(self):
        output=model()
        output["guidance_claims"]=[]
        output["analyst_focus"]={"qa_observed":"absent","question_blocks":[],"topics":[],"ambiguities":[]}
        output["management_tone"]["overall"]=insufficient()
        output["management_tone"]["prepared_remarks"]=insufficient()
        output["management_tone"]["qa"]=insufficient()
        output["management_tone"]["by_speaker"]=[]
        source=[turns()[0]]
        result=validate_analysis(output,source)
        self.assertEqual(result["derived"]["ranked_topics"],[])
        output["analyst_focus"]["qa_observed"]="present"
        with self.assertRaises(ValidationError):
            validate_analysis(output,source)

    def test_review_requires_resolvable_pointer_and_consistent_verdict(self):
        output=model(); source=turns()
        review={"verdict":"needs_changes","summary":"The topic response omits a caveat.","findings":[{"severity":"error","category":"analyst_focus","output_pointer":"/analyst_focus/topics/0","description":"Add the uncertainty caveat.","evidence":[{"turn_id":"t3","quote":"supplier pricing remains uncertain."}],"proposed_correction":"Include the caveat."}]}
        result=validate_review(review,output,source)
        self.assertEqual(result["derived"],{})
        review["findings"][0]["output_pointer"]="/guidance_claims/9"
        with self.assertRaises(ValidationError):
            validate_review(review,output,source)
        review["findings"][0]["output_pointer"]="/analyst_focus/topics/0"
        review["verdict"]="accepted"
        with self.assertRaises(ValidationError):
            validate_review(review,output,source)

    def test_guidance_numeric_shapes_and_unresolved_currency(self):
        for shape,values in (("point",{"point":"47"}),("range",{"low":"47","high":"48"}),
                             ("lower_bound",{"low":"47"}),("upper_bound",{"high":"48"})):
            with self.subTest(shape=shape):
                output=model();v=output["guidance_claims"][0]["value"]
                v.update(shape=shape,point=None,low=None,high=None,**{})
                v.update(values)
                validate_analysis(output,turns())
                v["scale"]="0"
                with self.assertRaises(ValidationError):validate_analysis(output,turns())
        for change in ({"point":"10"},{"low":"49"},{"scale":"NaN"},{"scale":"1e3"},{"currency":"usd"}):
            output=model();output["guidance_claims"][0]["value"].update(change)
            with self.subTest(change=change), self.assertRaises(ValidationError):
                validate_analysis(output,turns())
        output=model();output["guidance_claims"][0]["value"].update(unit="currency",currency=None)
        validate_analysis(output,turns())

    def test_withdrawal_does_not_become_numeric_guidance(self):
        output=model();claim=output["guidance_claims"][0];claim["management_action"]="withdrawn"
        with self.assertRaises(ValidationError):validate_analysis(output,turns())
        claim["value"].update(shape="none",point=None,low=None,high=None,scale=None)
        validate_analysis(output,turns())
        claim["target"].update(period_kind="calendar_year")
        with self.assertRaises(ValidationError):validate_analysis(output,turns())

    def test_answers_cannot_cross_question_boundaries_or_omit_turns(self):
        for values in (["t5"],[],["t3","t3"]):
            output=model();output["analyst_focus"]["question_blocks"][0]["management_answer_turn_ids"]=values
            with self.subTest(values=values),self.assertRaises(ValidationError):
                validate_analysis(output,turns())

    def test_speaker_tone_and_every_answer_quote_are_validated(self):
        output=model();output["management_tone"]["by_speaker"][0]["speaker_id"]="a1"
        with self.assertRaises(ValidationError):validate_analysis(output,turns())
        output=model();output["management_tone"]["by_speaker"][0]["assessment"]["supporting_evidence"][0]["quote"]="invented"
        with self.assertRaises(ValidationError):validate_analysis(output,turns())
        output=model();topic=output["analyst_focus"]["topics"][0]
        topic["response_status"]="not_answered";topic["answer_evidence"][0]["quote"]="invented"
        with self.assertRaises(ValidationError):validate_analysis(output,turns())
        output=model();output["analyst_focus"]["topics"].append(deepcopy(output["analyst_focus"]["topics"][0]))
        with self.assertRaises(ValidationError):validate_analysis(output,turns())

    def test_topic_unknown_counts_do_not_count_followups_as_unknown_people(self):
        source=turns();source[3]["speaker_id"]=source[1]["speaker_id"]
        result=validate_analysis(model(),source)["derived"]
        topic=result["ranked_topics"][0]
        self.assertEqual(topic["question_block_count"],2)
        self.assertEqual(topic["distinct_identified_analyst_count"],1)
        self.assertEqual(topic["unresolved_analyst_question_block_count"],0)
        source[3]["speaker_id"]=None
        result=validate_analysis(model(),source)["derived"]
        self.assertEqual(result["ranked_topics"][0]["unresolved_analyst_question_block_count"],1)

    def test_unknown_sections_remain_uncertain_and_tone_is_not_invented(self):
        source=[turns()[0]];source[0]["section"]="unknown"
        output=model()
        output["analyst_focus"]={"qa_observed":"uncertain","question_blocks":[],"topics":[],"ambiguities":[]}
        output["management_tone"]["overall"]=insufficient()
        output["management_tone"]["prepared_remarks"]=insufficient()
        output["management_tone"]["qa"]=insufficient()
        output["management_tone"]["by_speaker"]=[]
        validate_analysis(output,source)
        output["analyst_focus"]["qa_observed"]="absent"
        with self.assertRaises(ValidationError):validate_analysis(output,source)


    def test_courtesy_exclusion_keeps_source_and_answer_boundaries(self):
        source=turns()
        source.insert(3,{"turn_id":"courtesy","speaker_id":"a1","speaker_role":"analyst","section":"qa","text":"Great, thanks."})
        source.insert(4,{"turn_id":"welcome","speaker_id":"m1","speaker_role":"management","section":"qa","text":"You're welcome."})
        output=model()
        result=validate_analysis(output,source)
        self.assertEqual(result["derived"]["total_question_block_count"],2)
        self.assertEqual(result["derived"]["excluded_courtesy_block_count"],1)
        self.assertEqual(result["derived"]["ranked_topics"][0]["question_block_share"],"1")
        inventory=question_inventory(source)
        self.assertEqual(inventory["excluded_courtesy_blocks"],[["courtesy"]])
        self.assertEqual(inventory["question_blocks"][0]["management_answer_turn_ids"],["t3"])
        self.assertEqual(source[3]["text"],"Great, thanks.")
        output["analyst_focus"]["question_blocks"][0]["management_answer_turn_ids"].append("welcome")
        with self.assertRaises(ValidationError):validate_analysis(output,source)

    def test_courtesy_phrase_does_not_hide_an_actual_question(self):
        source=turns();source[1]["text"]="Thanks. What about costs?"
        inventory=question_inventory(source)
        self.assertEqual(len(inventory["question_blocks"]),2)
        self.assertEqual(inventory["excluded_courtesy_blocks"],[])


    def test_long_courtesy_near_match_finishes_and_preserves_question(self):
        import subprocess
        import sys
        script = (
            "from quant_data.company.transcript_analysis_contract import question_inventory;"
            "source=[dict(turn_id='t1',speaker_id='a1',speaker_role='analyst',section='qa',"
            "text='Thanks. '*10000+'What about costs?')];"
            "result=question_inventory(source);"
            "assert len(result['question_blocks'])==1;"
            "assert result['excluded_courtesy_blocks']==[]"
        )
        subprocess.run([sys.executable,"-c",script],cwd=ROOT,
            env={"PYTHONDONTWRITEBYTECODE":"1"},capture_output=True,check=True,timeout=5)

if __name__=="__main__":
    unittest.main()
