import os
import json
import anthropic
import stripe
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime
import re

app = Flask(__name__, static_folder='static')
CORS(app)

# Keys from environment variables — never hardcode these
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
STRIPE_PUBLISHABLE_KEY = os.environ.get('STRIPE_PUBLISHABLE_KEY')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')

SCAN_PRICE_PENCE = 1499  # £14.99 in pence

COMPLIANCE_PROMPT = """You are a senior marketing compliance analyst and consumer protection specialist.
You have deep expertise in UK ASA rules, FTC guidelines, GDPR, consumer protection law,
psychological manipulation detection, and contract vs. marketing claim consistency.

Scan the following materials for compliance risks and red flags.

MATERIALS:
{materials}

SCAN FOR:
1. CLAIM VS CONTRACT INCONSISTENCIES - promises in marketing that contradict T&Cs, guarantees vs refund policies, "free" claims with hidden conditions
2. PSYCHOLOGICAL MANIPULATION - false scarcity, artificial urgency, fake social proof, fear-based selling, inflated anchor prices
3. UNSUBSTANTIATED CLAIMS - income/results claims without disclaimers, health claims without evidence, superlatives without proof
4. LEGAL COMPLIANCE - missing required disclaimers, GDPR consent issues, misleading pricing, affiliate disclosure failures
5. CONTRACT RED FLAGS - one-sided cancellation, hidden auto-renewal, data usage broader than implied, liability contradictions

Respond ONLY with valid JSON:
{{
  "overall_risk_level": "HIGH"|"MEDIUM"|"LOW",
  "risk_score": <0-100>,
  "summary": "<2-3 sentence plain English summary>",
  "red_flags": [{{"id":1,"category":"<cat>","severity":"HIGH"|"MEDIUM"|"LOW","title":"<short title>","finding":"<what found>","location":"<where>","recommendation":"<fix>"}}],
  "inconsistencies": [{{"document_a":"<source>","claim_a":"<text>","document_b":"<source>","claim_b":"<text>","conflict":"<why problematic>"}}],
  "positive_findings": ["<compliant thing>"],
  "priority_actions": ["<top action 1>","<top action 2>","<top action 3>"]
}}"""


@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/config')
def get_config():
    return jsonify({
        'publishableKey': STRIPE_PUBLISHABLE_KEY,
        'price': SCAN_PRICE_PENCE
    })


@app.route('/create-payment-intent', methods=['POST'])
def create_payment_intent():
    try:
        intent = stripe.PaymentIntent.create(
            amount=SCAN_PRICE_PENCE,
            currency='gbp',
            automatic_payment_methods={'enabled': True},
            metadata={'product': 'redflagpro_scan'}
        )
        return jsonify({
            'clientSecret': intent['client_secret'],
            'paymentIntentId': intent['id']
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/verify-payment', methods=['POST'])
def verify_payment():
    try:
        data = request.json
        payment_intent_id = data.get('paymentIntentId')
        if not payment_intent_id:
            return jsonify({'error': 'No payment intent ID'}), 400
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        if intent.status == 'succeeded':
            return jsonify({'verified': True})
        else:
            return jsonify({'verified': False, 'status': intent.status}), 402
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/scan', methods=['POST'])
def run_scan():
    try:
        # Verify payment first
        payment_intent_id = request.form.get('paymentIntentId')
        if not payment_intent_id:
            return jsonify({'error': 'Payment required'}), 402

        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        if intent.status != 'succeeded':
            return jsonify({'error': 'Payment not confirmed'}), 402

        # Collect content from uploaded files and URLs
        materials_text = ''

        # Handle uploaded files
        files = request.files.getlist('files')
        for file in files:
            if file and file.filename:
                content = file.read().decode('utf-8', errors='ignore')
                materials_text += f"=== FILE: {file.filename} ===\n{content}\n\n"

        # Handle URLs
        urls = request.form.get('urls', '')
        if urls:
            for url in urls.split(','):
                url = url.strip()
                if url:
                    materials_text += f"=== URL: {url} ===\n[Analyse based on URL structure and any context provided]\n\n"

        # Handle pasted text
        pasted_text = request.form.get('text', '')
        if pasted_text:
            materials_text += f"=== PASTED CONTENT ===\n{pasted_text}\n\n"

        if not materials_text.strip():
            return jsonify({'error': 'No content provided to scan'}), 400

        # Run Claude analysis
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model='claude-opus-4-6',
            max_tokens=4096,
            messages=[{
                'role': 'user',
                'content': COMPLIANCE_PROMPT.format(materials=materials_text)
            }]
        )

        response_text = message.content[0].text
        clean = re.sub(r'```json\s*|\s*```', '', response_text).strip()

        try:
            results = json.loads(clean)
        except json.JSONDecodeError:
            results = {
                'overall_risk_level': 'UNKNOWN',
                'risk_score': 0,
                'summary': response_text,
                'red_flags': [],
                'inconsistencies': [],
                'positive_findings': [],
                'priority_actions': []
            }

        return jsonify(results)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
