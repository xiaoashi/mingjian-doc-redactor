# 公开样本来源与范围

以下来源用于本机功能验证。仓库不分发原图、第三方资料正文或个人申请表，只保留引用和可重复的合成样本生成脚本。

| 来源 | 用途 | 来源给出的许可/注意 |
|---|---|---|
| https://github.com/python-openxml/python-docx/blob/master/tests/test_files/test.docx | 基础DOCX兼容性 | 项目MIT许可；原件不作本软件自主源码。 |
| https://www.gutenberg.org/ebooks/23950 | 中文TXT及明确节选 | 来源标注美国公有领域；保留原书许可，其他地区使用依来源要求核实。 |
| https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf | 简单PDF文字/视觉对照 | W3C测试文件；仅作为基础兼容对照。 |
| https://colenda.library.upenn.edu/catalog/81431-p3806r | 约1600年的中文手写古籍页面1r、2r | University of Pennsylvania Libraries；清单Rights标记为NoC-US 1.0。 |
| https://github.com/PaddlePaddle/PaddleOCR/blob/main/deploy/ios_demo/PaddleOCRDemo/Resources/SampleImages/general_ocr_002.jpg | 公开打印照片场景 | 演示图可能另含第三方权利，未将原图放入公开库。 |

复杂DOCX与手写扫描PDF是上述公开资料的明确派生测试件。模糊、倾斜、低光照资料由 scripts/generate_ocr_fixtures.py 生成，并附合成字段。模拟图片不冒充真实相机拍摄。所有模型转写都需人工核对，手写图片不作为未经标注的精确率证明。
