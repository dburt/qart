# Decoder models

Not committed (binaries). The WeChat QR decoder in OpenCV 5 wants single-file ONNX models:

    gh release download -R shimat/caffe-ssd-to-onnx -p detect.onnx -p sr.onnx

Original Caffe weights (OpenCV 4.x API): https://github.com/WeChatCV/opencv_3rdparty/tree/wechat_qrcode
Without these files the WeChat decoder falls back to its classical detector.
