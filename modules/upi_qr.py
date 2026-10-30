"""
UPI QR Generator - Fixed price QR for each plan
UPI: kumaranil98787@axl
"""

import os
import io
import qrcode
from PIL import Image, ImageDraw, ImageFont
import urllib.parse

UPI_ID = os.getenv("UPI_ID", "kumaranil98787@axl").strip()
UPI_NAME = os.getenv("UPI_NAME", "OSINT Hunter Hub").strip()

def _q(s: str) -> str:
    return urllib.parse.quote(str(s), safe='')

def get_upi_link(amount: int, note: str = "OSINT Premium") -> str:
    if not UPI_ID:
        return ""
    return f"upi://pay?pa={_q(UPI_ID)}&pn={_q(UPI_NAME)}&am={amount}&cu=INR&tn={_q(note)}"

def generate_upi_qr(amount: int, plan_name: str = "Premium", note: str = "") -> io.BytesIO:
    """
    Generate UPI QR code with fixed price - ROBUST version for Render
    Returns BytesIO of PNG image - ALWAYS returns QR if possible
    """
    # Ensure UPI_ID exists
    upi_id = UPI_ID or os.getenv("UPI_ID", "kumaranil98787@axl").strip() or "kumaranil98787@axl"
    upi_name = UPI_NAME or "OSINT Hunter Hub"
    
    upi_link = get_upi_link(amount, note or f"OSINT {plan_name}")
    if not upi_link:
        upi_link = f"upi://pay?pa={_q(upi_id)}&pn={_q(upi_name)}&am={amount}&cu=INR&tn={_q(note or plan_name)}"
    
    # Try to generate QR - multiple fallbacks
    for attempt in range(3):
        try:
            if attempt == 0:
                # Full version with text overlay - BEST
                qr = qrcode.QRCode(
                    version=1,
                    error_correction=qrcode.constants.ERROR_CORRECT_H,
                    box_size=10,
                    border=4,
                )
                qr.add_data(upi_link)
                qr.make(fit=True)
                qr_img = qr.make_image(fill_color="black", back_color="white").convert('RGB')
                width, height = qr_img.size
                extra_height = 140
                final_img = Image.new('RGB', (width, height + extra_height), 'white')
                final_img.paste(qr_img, (0, 0))
                draw = ImageDraw.Draw(final_img)
                try:
                    font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
                    font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
                    font_tiny = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
                except:
                    font_large = ImageFont.load_default()
                    font_small = ImageFont.load_default()
                    font_tiny = ImageFont.load_default()
                
                text_y = height + 8
                try:
                    draw.text((10, text_y), f"{plan_name}", fill="black", font=font_large)
                    draw.text((10, text_y + 28), f"Amount: ₹{amount} (Fixed)", fill="black", font=font_small)
                    draw.text((10, text_y + 50), f"UPI: {upi_id}", fill="black", font=font_small)
                    draw.text((10, text_y + 72), f"Scan with PhonePe/GPay/Paytm", fill="black", font=font_small)
                    draw.text((10, text_y + 94), f"Note: {note or plan_name[:30]}", fill="gray", font=font_tiny)
                    draw.text((10, text_y + 112), f"Powered by @ANONYMOUS_XH", fill="gray", font=font_tiny)
                except Exception as te:
                    print(f"QR text draw failed attempt {attempt}: {te}")
                    draw.text((10, text_y), f"{plan_name} - Rs.{amount}", fill="black")
                    draw.text((10, text_y + 25), f"UPI: {upi_id}", fill="black")
                    draw.text((10, text_y + 50), f"Scan to Pay", fill="black")
                
                bio = io.BytesIO()
                final_img.save(bio, 'PNG')
                bio.seek(0)
                print(f"QR generated BEST attempt {attempt}: {plan_name} ₹{amount} -> {len(bio.getvalue())} bytes")
                return bio
            elif attempt == 1:
                # Simple QR without text overlay - fallback
                qr = qrcode.QRCode(version=1, box_size=10, border=4)
                qr.add_data(upi_link)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                bio = io.BytesIO()
                img.save(bio, 'PNG')
                bio.seek(0)
                print(f"QR generated SIMPLE attempt {attempt}: {len(bio.getvalue())} bytes")
                return bio
            else:
                # Ultra simple - no PIL conversion
                qr = qrcode.make(upi_link)
                bio = io.BytesIO()
                qr.save(bio, 'PNG')
                bio.seek(0)
                print(f"QR generated ULTRA attempt {attempt}: {len(bio.getvalue())} bytes")
                return bio
        except Exception as e:
            print(f"QR attempt {attempt} failed: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    print(f"All QR generation attempts failed for {plan_name} ₹{amount}")
    return None

def generate_qr_file(amount: int, plan_name: str, filepath: str) -> bool:
    """Generate QR and save to file"""
    try:
        bio = generate_upi_qr(amount, plan_name)
        if bio:
            with open(filepath, 'wb') as f:
                f.write(bio.getvalue())
            return True
        return False
    except Exception as e:
        print(f"QR file save failed: {e}")
        return False
