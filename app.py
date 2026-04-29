import os
import json
import anthropic
import stripe
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='static')
CORS(app)

stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
STRIPE_PUBLISHABLE_KEY = os.environ.get('STRIPE_PUBLISHABLE_KEY')

print(f"Anthropic key loaded: {bool(ANTHROPIC_API_KEY)}")
print(f"Stripe key loaded: {bool(stripe.api_key)}")

SCAN_PRICE_PENCE = 1499


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


@app.route('/scan', methods=['POST'])
def run_scan():
    try:
        payment_intent_id = request.form.get('paymentIntentId')
        if not payment_intent_id:
            return jsonify({'error': 'Payment required'}), 402

        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        if intent.status != 'succeeded':
            return jsonify({'error': 'Payment not confirmed'}), 402

        materials_text = ''

        files = request.files.getlist('files')
        for file in files:
            if file and file.filename:
                content = file.read().decode('utf-8', errors='ignore')
                materials_text += f"=== FILE: {file.filename} ===\n{content}\n\n"

        urls = request.form.get('urls', '')
        if urls:
            for url in urls.split(','):
                url = url.strip()
                if url:
                    materials_text += f"=== URL: {url} ===\n[Analyse this URL for compliance issues]\n\n"

        pasted_text = request.form.get('text', '')
        if pasted_text:
            materials_text += f"=== PASTED CONTENT ===\n{pasted_text}\n\n"

        if not materials_text.strip():
            return jsonify({'error': 'No content provided to scan'}), 400

        print(f"Scanning {len(materials_text)} characters")
        print(f"Preview: {materials_text[:200]}")

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=2048,
            messages=[{
                'role': 'user',
                'content': f"""You are a marketing compliance analyst. Analyse this content for compliance risks.

CONTENT:
{materials_text}

Respond with ONLY valid JSON, no other text:

{{"overall_risk_level": "HIGH", "risk_score": 85, "summary": "summary here", "red_flags": [{{"id": 1, "category": "Category", "severity": "HIGH", "title": "Title", "finding": "Finding", "location": "Location", "recommendation": "Fix"}}], "inconsistencies": [], "positive_findings": ["positive"], "priority_actions": ["action 1", "action 2"]}}"""
            }]
        )

        response_text = message.content[0].text.strip()
        print(f"Claude response length: {len(response_text)}")
        print(f"Claude response: {response_text[:500]}")

        if '```' in response_text:
            parts = response_text.split('```')
            for part in parts:
                if part.startswith('json'):
                    response_text = part[4:].strip()
                    break
                elif '{' in part:
                    response_text = part.strip()
                    break

        results = json.loads(response_text)
        return jsonify(results)

    except json.JSONDecodeError as e:
        print(f"JSON error: {e}")
        return jsonify({'error': 'Failed to parse results'}), 500
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
