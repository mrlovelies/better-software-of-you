# Google Docs API Patterns

## Endpoints

- **Create:** `POST https://docs.googleapis.com/v1/documents` with `{"title": "..."}`
- **Read:** `GET https://docs.googleapis.com/v1/documents/{documentId}`
- **Update:** `POST https://docs.googleapis.com/v1/documents/{documentId}:batchUpdate`

## Auth Header

All requests require: `Authorization: Bearer {access_token}`

## Creating a Document

```json
POST https://docs.googleapis.com/v1/documents
{"title": "My Document"}
```

Returns a document object with `documentId` which is used for all subsequent operations.

## Inserting Text

Text is inserted at a specific index. Index 1 is the start of the document body (index 0 is reserved).

```json
POST https://docs.googleapis.com/v1/documents/{id}:batchUpdate
{
  "requests": [
    {
      "insertText": {
        "location": {"index": 1},
        "text": "Hello world\n\nThis is a paragraph."
      }
    }
  ]
}
```

## Replacing All Content

To replace all content, first delete everything then insert new text:

```json
{
  "requests": [
    {"deleteContentRange": {"range": {"startIndex": 1, "endIndex": END_INDEX}}},
    {"insertText": {"location": {"index": 1}, "text": "New content here"}}
  ]
}
```

Get `END_INDEX` from reading the document first: look at the last element's `endIndex` in `body.content`, subtract 1.

## Formatting Text

After inserting text, format ranges:

```json
{
  "requests": [
    {
      "updateTextStyle": {
        "range": {"startIndex": 1, "endIndex": 12},
        "textStyle": {"bold": true},
        "fields": "bold"
      }
    }
  ]
}
```

## Paragraph Styles (Headers)

```json
{
  "requests": [
    {
      "updateParagraphStyle": {
        "range": {"startIndex": 1, "endIndex": 20},
        "paragraphStyle": {"namedStyleType": "HEADING_1"},
        "fields": "namedStyleType"
      }
    }
  ]
}
```

Named styles: `NORMAL_TEXT`, `HEADING_1` through `HEADING_6`, `TITLE`, `SUBTITLE`.

## Document Structure

The Docs API returns a document with `body.content` — an array of structural elements:

```
document.body.content[].paragraph.elements[].textRun.content
```

Each `textRun` has `.content` (the text) and `.textStyle` (formatting).
