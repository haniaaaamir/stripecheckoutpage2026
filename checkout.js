document.getElementById('payment-form').addEventListener('submit', async (e) => {
  e.preventDefault();

  const numberOfKids = parseInt(document.getElementById('num-kids').value);
  const paymentType = document.getElementById('payment-type').value;

  if (isNaN(numberOfKids) || numberOfKids < 1 || numberOfKids > 5) {
    alert("Please enter a valid number of children (1–5).");
    return;
  }

  const response = await fetch("/create-checkout-session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      number_of_kids: numberOfKids,
      payment_type: paymentType
    })
  });

  const data = await response.json();

  if (data.error) {
    alert("Error: " + data.error);
    return;
  }

  // Redirect to Stripe Checkout page
  window.location.href = data.url;
});
