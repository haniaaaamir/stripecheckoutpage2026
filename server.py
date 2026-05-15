#! /usr/bin/env python3.6

import os
import time
from flask import Flask, jsonify, redirect, request, abort
import stripe

stripe.api_key = os.environ['STRIPE_SECRET_KEY']

app = Flask(__name__, static_url_path='', static_folder='public')

YOUR_DOMAIN = os.environ.get("YOUR_DOMAIN")
PRODUCT_ID = os.environ['PRODUCT_ID']
WEBHOOK_SECRET = os.environ['WEBHOOK_SECRET']

MAX_KIDS = 5
MAX_BIWEEKLY_PAYMENTS = 3

# updated pricing
YOUNGER_FIRST = 425
YOUNGER_SIBLING = 400

OLDER_FIRST = 375
OLDER_SIBLING = 350


def calculate_total_price(ages):

    if len(ages) < 1:
        raise ValueError("Must register at least one kid.")

    if len(ages) > MAX_KIDS:
        raise ValueError(f"Cannot register more than {MAX_KIDS} kids.")

    total = 0
    younger_count = 0
    older_count = 0

    for age in ages:

        # Younger group (7-12)
        if 7 <= age <= 12:
            if younger_count == 0:
                total += YOUNGER_FIRST
            else:
                total += YOUNGER_SIBLING
            younger_count += 1

        # Older group (13-18)
        elif 13 <= age <= 18:
            if older_count == 0:
                total += OLDER_FIRST
            else:
                total += OLDER_SIBLING
            older_count += 1

        else:
            raise ValueError(f"Invalid age: {age}")

    return total


def create_biweekly_price(amount_cents):
    """
    Create a dynamic recurring price in Stripe for biweekly payments.
    amount_cents = total amount for one payment (total_price / 3)
    """
    price = stripe.Price.create(
        unit_amount=amount_cents,
        currency='cad',
        recurring={'interval': 'week', 'interval_count': 2},
        product=PRODUCT_ID,
    )
    return price.id


@app.route('/redirect-to-checkout', methods=['GET'])
def redirect_to_checkout():
    try:
        payment_type = request.args.get('payment_type', 'full').lower()
        # get ages for each child
        ages = []

        for i in range(1, MAX_KIDS + 1):
            age_value = request.args.get(f'age{i}')
            # Skip empty values (None or empty string)
            if age_value and age_value.strip():
                ages.append(int(age_value))

        total_price = calculate_total_price(ages)
        total_cents = int(total_price * 100)

        if payment_type == 'full':
            price = stripe.Price.create(
                unit_amount=total_cents,
                currency='cad',
                product=PRODUCT_ID,
            )
            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price': price.id,
                    'quantity': 1,
                }],
                mode='payment',
                success_url=YOUR_DOMAIN + '/return.html?session_id={CHECKOUT_SESSION_ID}',
                cancel_url=YOUR_DOMAIN + '/checkout.html',
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
                success_url=YOUR_DOMAIN + '/return.html?session_id={CHECKOUT_SESSION_ID}',
                cancel_url=YOUR_DOMAIN + '/checkout.html',
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

        payment_type = data.get('payment_type', 'full').lower()
        if payment_type not in ['full', 'biweekly']:
            raise ValueError("Invalid payment type. Must be full or biweekly.")

        ages = []

        for i in range(1, MAX_KIDS + 1):
            age_value = data.get(f'age{i}', '').strip()
            
            # Skip empty values
            if not age_value:
                continue
                
            if not age_value.isdigit():
                raise ValueError(f"age{i} must be a number")

            age = int(age_value)

            if age < 7 or age > 18:
                raise ValueError(f"age{i} must be between 7 and 18")

            ages.append(age)

        if len(ages) == 0:
            raise ValueError("At least one kid is required")

        total_price = calculate_total_price(ages)

        return jsonify({
            "status": "received",
            "ages": ages,
            "payment_type": payment_type,
            "total_price": total_price
        }), 200

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
            return '', 200

        subscription = stripe.Subscription.retrieve(subscription_id)
        paid_cycles = int(subscription.metadata.get('paid_cycles', '0'))
        max_cycles = int(subscription.metadata.get('max_cycles', str(MAX_BIWEEKLY_PAYMENTS)))

        if paid_cycles >= max_cycles:
            stripe.Invoice.void_invoice(invoice['id'])
            return '', 200

    elif event_type == 'invoice.paid':
        invoice = event['data']['object']
        subscription_id = invoice.get('subscription')
        if not subscription_id:
            return '', 200

        subscription = stripe.Subscription.retrieve(subscription_id)
        paid_cycles = int(subscription.metadata.get('paid_cycles', '0')) + 1
        max_cycles = int(subscription.metadata.get('max_cycles', str(MAX_BIWEEKLY_PAYMENTS)))

        stripe.Subscription.modify(
            subscription_id,
            metadata={
                'paid_cycles': str(paid_cycles),
                'max_cycles': str(max_cycles)
            }
        )

        if paid_cycles >= max_cycles:
            stripe.Subscription.delete(subscription_id)

    elif event_type == 'invoice.payment_failed':
        invoice = event['data']['object']
        print(f"Payment failed for {invoice.get('subscription')}")

    return '', 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 4242)))
