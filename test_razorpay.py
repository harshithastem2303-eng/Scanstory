import os
from dotenv import load_dotenv
import razorpay

load_dotenv()

RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET")

print("=" * 50)
print("Testing Razorpay Configuration")
print("=" * 50)

if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
    print("❌ ERROR: Razorpay keys are missing in .env file")
    print(f"Key ID: {'❌ MISSING' if not RAZORPAY_KEY_ID else '✅ PRESENT'}")
    print(f"Key Secret: {'❌ MISSING' if not RAZORPAY_KEY_SECRET else '✅ PRESENT (hidden)'}")
    exit(1)

print(f"✅ Key ID: {RAZORPAY_KEY_ID}")
print(f"✅ Key Secret: {'*' * len(RAZORPAY_KEY_SECRET)}")

# Test Razorpay connection
try:
    client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    
    # Test with a simple API call
    test_amount = 100  # 1 rupee in paise
    test_order_data = {
        'amount': test_amount,
        'currency': 'INR',
        'payment_capture': 1,
        'notes': {'test': 'true'}
    }
    
    print("\n📋 Creating test order...")
    test_order = client.order.create(data=test_order_data)
    
    print(f"✅ Test successful!")
    print(f"✅ Order ID: {test_order['id']}")
    print(f"✅ Amount: ₹{test_order['amount'] / 100}")
    print(f"✅ Status: {test_order['status']}")
    
    # Clean up test order (optional)
    # client.order.cancel(test_order['id'])
    # print("✅ Test order cancelled")
    
except razorpay.errors.AuthenticationError as e:
    print(f"❌ AUTHENTICATION FAILED: {e}")
    print("Please check your RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET")
    print("Make sure you're using the correct keys (test vs live)")
    
except razorpay.errors.BadRequestError as e:
    print(f"❌ BAD REQUEST: {e}")
    print("This might be due to invalid request parameters")
    
except Exception as e:
    print(f"❌ UNEXPECTED ERROR: {e}")
    print("Please check your internet connection and Razorpay status")

print("\n" + "=" * 50)
print("Test Complete")
print("=" * 50)