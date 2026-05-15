#! /usr/bin/env python3.6

import os
import time
from flask import Flask, jsonify, redirect, request, abort
import stripe

stripe.api_key = os.environ['STRIPE_SECRET_KEY']

app = Flask(__name__, static_url_path='', static_folder='public')

YOUR_DOMAIN = 'https://stripecheckout-jotform.onrender.com'
PRODUCT_ID = os.environ['PRODUCT_ID']
WEBHOOK_SECRET = os.environ['WEBHOOK_SECRET']  

MAX_KIDS = 5
MAX_BIWEEKLY_PAYMENTS = 3

def calculate_total_price(number_of_kids):
    # Discounted price formula: 425 + (kids -1) * 400
    if number_of_kids < 1:
        raise ValueError("Must register at least one kid.")
    if number_of_kids > MAX_KIDS:
        raise ValueError(f"Cannot register more than {MAX_KIDS} kids.")
    return (425 + (number_of_kids - 1) * 400) * 1.029

def create_biweekly_price(amount_cents):
    """
    Create a dynamic recurring price in Stripe for biweekly payments.
    amount_cents = total amount for one payment (total_price / 3)
    """
    price = stripe.Price.create(
        unit_amount=amount_cents,
        currency='cad',
        recurring={'interval': 'week', 'interval_count': 2},
        product= PRODUCT_ID,
    )
    return price.id


@app.route('/redirect-to-checkout', methods=['GET'])
def redirect_to_checkout():
    try:
        number_of_kids = int(request.args.get('number_of_kids', 1))
        payment_type = request.args.get('payment_type', 'full').lower()

        total_price = calculate_total_price(number_of_kids)
        total_cents = int(total_price * 100)

        if payment_type == 'full':
            price = stripe.Price.create(
                unit_amount=total_cents,
                currency='cad',
                product= PRODUCT_ID,
            )
            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price': price.id,
                    'quantity': 1,
                }],
                mode='payment',
                success_url='https://stripecheckout-jotform.onrender.com'+ '/return.html?session_id={CHECKOUT_SESSION_ID}',
                cancel_url='https://stripecheckout-jotform.onrender.com' + '/checkout.html',
            )
        elif payment_type == 'biweekly':
            per_payment_cents = total_cents // MAX_BIWEEKLY_PAYMENTS
            price_id = create_biweekly_price(per_payment_cents)

            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price': price_id,
                    'quantity': 1,
                }],
                mode='subscription',
                subscription_data={
                    'metadata': {
                        'paid_cycles': '0',
                        'max_cycles': str(MAX_BIWEEKLY_PAYMENTS),
                    }
                },
                success_url='https://stripecheckout-jotform.onrender.com' + '/return.html?session_id={CHECKOUT_SESSION_ID}',
                cancel_url='https://stripecheckout-jotform.onrender.com' + '/checkout.html',
            )
        else:
            return jsonify({"error": "Invalid payment type"}), 400

        return redirect(session.url)

    except Exception as e:
        return jsonify(error=str(e)), 400


@app.route('/jotform-hook', methods=['POST'])
def jotform_hook():
    try:
        data = request.form.to_dict()
        print("Received Jotform data:", data)

        # Example: Extract key fields from Jotform
        raw_kids = data.get('number_of_kids', '').strip()
        if not raw_kids.isdigit():
            raise ValueError("number_of_kids must be a number")

        number_of_kids = int(raw_kids)
        if number_of_kids < 1 or number_of_kids > MAX_KIDS:
            raise ValueError("Number of kids must be between 1 & 5")
        payment_type = data.get('payment_type', 'full').lower()
        if payment_type not in ['full', 'biweekly']:
            raise ValueError("Invalid payment type. Must be full or biweekly.")

        return jsonify({"status": "received", "number_of_kids": number_of_kids, "payment_type": payment_type}), 200
        
    except Exception as e:
        return jsonify(error=str(e)), 400

@app.route('/session-status', methods=['GET'])
def session_status():
    session_id = request.args.get('session_id')
    if not session_id:
        return jsonify(error='session_id required'), 400
    session = stripe.checkout.Session.retrieve(session_id)
    return jsonify(status=session.status, customer_email=session.customer_details.email)

@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get('stripe-signature')

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        return 'Invalid payload or signature', 400

    event_type = event['type']

    if event_type == 'invoice.created':
        invoice = event['data']['object']
        subscription_id = invoice.get('subscription')
        if not subscription_id:
            return '', 200  # Not a subscription invoice

        subscription = stripe.Subscription.retrieve(subscription_id)
        paid_cycles = int(subscription.metadata.get('paid_cycles', '0'))
        max_cycles = int(subscription.metadata.get('max_cycles', str(MAX_BIWEEKLY_PAYMENTS)))

        if paid_cycles >= max_cycles:
            stripe.Invoice.void_invoice(invoice['id'])
            print(f"Voided invoice for {subscription_id} — max cycles reached.")
            return '', 200

    elif event_type == 'invoice.paid':
        invoice = event['data']['object']
        subscription_id = invoice.get('subscription')
        if not subscription_id:
            return '', 200

        subscription = stripe.Subscription.retrieve(subscription_id)
        paid_cycles = int(subscription.metadata.get('paid_cycles', '0')) + 1
        max_cycles = int(subscription.metadata.get('max_cycles', str(MAX_BIWEEKLY_PAYMENTS)))

        # Update metadata
        stripe.Subscription.modify(
            subscription_id,
            metadata={
                'paid_cycles': str(paid_cycles),
                'max_cycles': str(max_cycles)
            }
        )

        print(f"Payment #{paid_cycles} received for {subscription_id}")

        if paid_cycles >= max_cycles:
            stripe.Subscription.delete(subscription_id)
            print(f"Payment {subscription_id} stopped after {paid_cycles} cycles.")

    elif event_type == 'invoice.payment_failed':
        invoice = event['data']['object']
        print(f"Payment failed for {invoice.get('subscription')}")

    return '', 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 4242)))
